// parrot-speech-runtime: Whisper Face's own Parakeet Unified runtime.
//
// It speaks the same wire protocol as native/ParrotASRHelper (see
// native/ParrotASRHelper/PROTOCOL.md): launched the old way it answers in
// protocol 1, exactly as that helper does; asked with `--protocol 2` it adds
// each word's time and confidence. It loads the pinned Core ML files from
// the folder it is given and from nowhere else. It has no way to download:
// it uses no networking API, and a missing or broken model file makes it
// print {"error":"model load failed","ok":false} and exit 1, so the app
// falls back. It never deletes, moves or writes a file.

import Foundation

let arguments = Array(CommandLine.arguments.dropFirst())
let mode = arguments.first ?? "--server"
var knownModes = ["--server", "--preload", "--verify"]
#if DEBUG
// A debug build can also dump what the joint network offered at every
// decode step for one clip. A release build, which is what setup.sh
// installs, has no such mode.
knownModes.append("--dump-candidates")
#endif
guard knownModes.contains(mode) else {
    FileHandle.standardError.write(Data(Wire.usage.utf8))
    exit(2)
}
let wire = Wire.negotiatedProtocol(arguments)

let started = ContinuousClock.now
let recognizer: Recognizer
do {
    recognizer = try Recognizer(modelDirectory: Wire.modelDirectory(arguments))
    // One decode of a second of silence runs every model once, so a file
    // that loads but cannot run fails here, before the runtime says it is
    // ready, and not in the middle of someone's dictation.
    _ = try autoreleasepool {
        try recognizer.transcribe([Float](repeating: 0, count: Wire.sampleRate))
    }
} catch {
    Wire.emit(["ok": false, "error": "model load failed"])
    exit(1)
}
var status: [String: Any] = [
    "model": Wire.model,
    "load_s": Wire.seconds(started.duration(to: .now)),
]
if wire >= 2 {
    status["protocol"] = wire
    status["capabilities"] = ["words", "vocabulary"]
    status["engine"] = Wire.engine
}

switch mode {
case "--server":
    status["ready"] = true
    Wire.emit(status)
    exit(serve(recognizer, wire: wire))
#if DEBUG
case "--dump-candidates":
    exit(dumpCandidates(recognizer, arguments: arguments))
#endif
default:
    status["ok"] = true
    Wire.emit(status)
    exit(0)
}

/// Answer framed requests until the input ends. 0 at a clean end; 1 when a
/// request is cut off part way.
func serve(_ recognizer: Recognizer, wire: Int) -> Int32 {
    while true {
        // Everything one request allocates, Core ML's results included, is
        // released before the next request is read.
        if let exitCode = autoreleasepool(invoking: { serveOne(recognizer, wire: wire) }) {
            return exitCode
        }
    }
}

/// Read and answer one request: nil to go on, or the exit code.
func serveOne(_ recognizer: Recognizer, wire: Int) -> Int32? {
    var rawCount: UInt64 = 0
    let headerRead = withUnsafeMutableBytes(of: &rawCount) {
        Wire.readFully(fileDescriptor: 0, into: $0)
    }
    if headerRead == 0 { return 0 }
    guard headerRead == 8 else { return 1 }
    let requested = UInt64(littleEndian: rawCount)
    if wire >= 2, requested & Wire.controlFlag != 0 {
        return serveControl(recognizer, length: requested & ~Wire.controlFlag)
    }
    guard requested > 0, requested <= UInt64(Wire.maximumSamples) else {
        Wire.emit(["ok": false, "error": "invalid sample count"])
        return nil
    }
    let count = Int(requested)
    var samples = [Float](repeating: 0, count: count)
    let payloadRead = samples.withUnsafeMutableBytes {
        Wire.readFully(fileDescriptor: 0, into: $0)
    }
    guard payloadRead == count * 4 else { return 1 }
    let requestStarted = ContinuousClock.now
    do {
        let transcript = try recognizer.transcribe(samples)
        var response: [String: Any] = [
            "ok": true,
            "text": transcript.text,
            "processing_s": Wire.seconds(requestStarted.duration(to: .now)),
        ]
        if wire >= 2 {
            WordEvidence.add(
                transcript, sampleCount: count,
                vocabulary: recognizer.vocabulary.pieces, to: &response)
            TermBias.add(transcript, recognizer: recognizer, to: &response)
        }
        Wire.emit(response)
    } catch {
        Wire.emit(["ok": false, "error": "transcription failed: \(error)"])
    }
    return nil
}

/// Answer one control frame (protocol 2): nil to go on, or the exit code.
/// The only operation is `vocabulary`, which sets the terms the term pass
/// looks for and its budget; no terms, or a budget of zero, switches the
/// pass off. A frame too long to be one ends the process, as a request cut
/// off part way does, because what follows it cannot be found.
func serveControl(_ recognizer: Recognizer, length: UInt64) -> Int32? {
    guard length > 0, length <= Wire.maximumControlBytes else { return 1 }
    var payload = [UInt8](repeating: 0, count: Int(length))
    let read = payload.withUnsafeMutableBytes {
        Wire.readFully(fileDescriptor: 0, into: $0)
    }
    guard read == Int(length) else { return 1 }
    guard let frame = try? JSONSerialization.jsonObject(with: Data(payload)) as? [String: Any],
          let op = frame["op"] as? String else {
        Wire.emit(["ok": false, "error": "invalid control frame"])
        return nil
    }
    guard op == "vocabulary",
          let version = frame["version"] as? Int,
          let entries = frame["terms"] as? [[String: Any]],
          let budget = frame["max_debt"] as? Double else {
        Wire.emit(["ok": false, "op": op, "error": "invalid control frame"])
        return nil
    }
    let texts = entries.compactMap { $0["text"] as? String }
    let list = TermList(
        version: version, terms: texts, maxDebt: budget,
        vocabulary: recognizer.vocabulary.pieces)
    recognizer.termList = list
    Wire.emit([
        "ok": true, "op": op, "version": version,
        "terms": list?.terms.count ?? 0,
        "dropped": list?.dropped ?? entries.count,
    ])
    return nil
}

#if DEBUG
/// Debug builds only: decode one clip and print, one JSON line per joint
/// step, what the model chose and the 64 candidates it offered, then the
/// transcript. The clip is raw little-endian float32, 16 kHz mono, named by
/// `--audio FILE`. Nothing printed here changes what is decoded.
func dumpCandidates(_ recognizer: Recognizer, arguments: [String]) -> Int32 {
    guard let path = Wire.value(after: "--audio", in: arguments),
          let data = FileManager.default.contents(atPath: path),
          data.count >= 4, data.count % 4 == 0,
          data.count / 4 <= Wire.maximumSamples else {
        FileHandle.standardError.write(Data(
            "usage: parrot-speech-runtime --dump-candidates --audio FILE.f32 [--models DIR]\n".utf8))
        return 2
    }
    var samples = [Float](repeating: 0, count: data.count / 4)
    _ = samples.withUnsafeMutableBytes { data.copyBytes(to: $0) }
    let pieces = recognizer.vocabulary.pieces
    func piece(_ id: Int) -> String { pieces[id] ?? (id == recognizer.decoder.blankId ? "<blank>" : "") }
    recognizer.decoder.stepObserver = { step in
        Wire.emit([
            "window_start_frame": step.windowStartFrame,
            "frame": step.frame,
            "symbol": step.symbol,
            "chosen": step.chosen,
            "chosen_piece": piece(step.chosen),
            "probability": WordEvidence.rounded(Double(step.probability)),
            "ids": step.candidates.ids,
            "pieces": step.candidates.ids.map(piece),
            "logits": step.candidates.logits.map { WordEvidence.rounded(Double($0)) },
        ])
    }
    defer { recognizer.decoder.stepObserver = nil }
    do {
        let transcript = try recognizer.transcribe(samples)
        Wire.emit(["done": true, "text": transcript.text])
        return 0
    } catch {
        Wire.emit(["ok": false, "error": "transcription failed: \(error)"])
        return 1
    }
}
#endif
