// Parakeet Unified 0.6B, offline: the pinned Core ML files, 15 s windows,
// and the merged transcript.
//
// Derived from FluidAudio (https://github.com/FluidInference/FluidAudio),
// tag v0.15.5, commit 19600a485baa4998812e4654b70d2bab8f2c9949:
// Sources/FluidAudio/ASR/Parakeet/Unified/UnifiedAsrManager.swift
// (UnifiedBatchLayout, loadModels(from:), transcribe(_:),
// transcribeWindow), Sources/FluidAudio/ASR/Parakeet/Unified/
// UnifiedConfig.swift, Sources/FluidAudio/Shared/
// MLModelConfigurationUtils.swift and the ParakeetUnified file names in
// Sources/FluidAudio/ModelNames.swift. Copyright the FluidAudio authors.
// Licensed under the Apache License, Version 2.0
// (native/ParrotSpeechRuntime/LICENSE-Apache-2.0.txt).
//
// Changed for Whisper Face: models load only from the folder given, by
// file path, and never through a download or cache-repair path; a missing
// or damaged file is an error. The encoder, decoder and joint run on the
// compute units the library chose for the int8 encoder (CPU and Neural
// Engine; CPU only for the per-token models). The engine is synchronous,
// the streaming and fp16 variants are dropped, and the merged token stream
// is returned with its frames so word timings can be built from it.

import CoreML
import Foundation

enum RuntimeError: Error, CustomStringConvertible {
    case modelFiles(String)
    case decoding(String)

    var description: String {
        switch self {
        case .modelFiles(let detail): return "model files: \(detail)"
        case .decoding(let detail): return "decoding: \(detail)"
        }
    }
}

/// The pinned model's files, relative to its folder.
enum ModelFiles {
    static let encoder = "parakeet_unified_encoder_int8.mlmodelc"
    static let decoder = "parakeet_unified_decoder.mlmodelc"
    static let joint = "parakeet_unified_joint_decision_single_step.mlmodelc"
    static let vocabulary = "vocab.json"
    static let all = [encoder, decoder, joint, vocabulary]
}

/// Window arithmetic for the fixed 15 s encoder export: frame-aligned
/// windows that overlap by 2 s.
struct WindowLayout {
    let sampleRate = 16_000
    /// Samples per encoder frame (80 ms).
    let frameSamples = 1280

    /// The encoder's fixed input: 15 s.
    var windowSamples: Int { 15 * sampleRate }
    /// Audio decoded per window, frame aligned (14.96 s).
    var chunkSamples: Int { windowSamples / frameSamples * frameSamples }
    /// Frame-aligned overlap between adjacent windows (2 s).
    var overlapSamples: Int {
        let requested = 2 * sampleRate
        return min(requested, chunkSamples / 2) / frameSamples * frameSamples
    }
    var strideSamples: Int { chunkSamples - overlapSamples }

    /// Start offsets of every window needed to cover `totalSamples`.
    func chunkStarts(totalSamples: Int) -> [Int] {
        guard totalSamples > 0 else { return [] }
        var starts = [0]
        var start = strideSamples
        while start < totalSamples {
            // A window is needed only if it adds samples beyond the last.
            if start + overlapSamples < totalSamples {
                starts.append(start)
            }
            start += strideSamples
        }
        return starts
    }
}

/// A decoded recording: its text and the merged token stream behind it,
/// and, when the user's terms were set, what the term pass found.
struct Transcript {
    let text: String
    let tokens: [TokenWindow]
    var termList: TermList? = nil
    var detours: [Detour] = []
    var termPass = TermPassStats()
}

/// One recording's term pass: the list it runs with, read once when the
/// recording starts (a request, or a session's `begin`), and what it has
/// found in the windows decoded so far. With no list nothing runs.
struct TermPassState {
    let list: TermList?
    var detours: [Detour] = []
    var stats = TermPassStats()
}

final class Recognizer {
    let layout = WindowLayout()
    let vocabulary: Vocabulary
    let decoder: TransducerDecoder
    private let encoder: MLModel
    private let mel: MelFeatures
    private let spliceSafeTokenIds: Set<Int>?
    private let caseVariantIds: [Int: Int]?
    /// The user's terms, set by a vocabulary control frame. Nil, the
    /// default, runs no term pass and records nothing extra.
    var termList: TermList?

    /// Load the four model files from `directory`. Nothing is downloaded,
    /// created, moved or deleted: a file that is missing or will not load
    /// is an error, and the caller exits.
    init(modelDirectory directory: String) throws {
        let fileManager = FileManager.default
        for name in ModelFiles.all {
            let path = (directory as NSString).appendingPathComponent(name)
            guard fileManager.fileExists(atPath: path) else {
                throw RuntimeError.modelFiles("\(name) is missing")
            }
        }
        func url(_ name: String) -> URL {
            URL(fileURLWithPath: (directory as NSString).appendingPathComponent(name),
                isDirectory: name.hasSuffix(".mlmodelc"))
        }

        // The library's default for this encoder: CPU and Neural Engine,
        // low-precision GPU accumulation allowed (the GPU is not used: int8
        // ops routed to it abort in MPSGraph). The per-token decoder and
        // joint run on the CPU, where single steps are fastest.
        let encoderConfig = MLModelConfiguration()
        encoderConfig.allowLowPrecisionAccumulationOnGPU = true
        encoderConfig.computeUnits = .cpuAndNeuralEngine
        let cpuConfig = MLModelConfiguration()
        cpuConfig.computeUnits = .cpuOnly

        do {
            self.encoder = try MLModel(contentsOf: url(ModelFiles.encoder), configuration: encoderConfig)
            let decoderModel = try MLModel(contentsOf: url(ModelFiles.decoder), configuration: cpuConfig)
            let jointModel = try MLModel(contentsOf: url(ModelFiles.joint), configuration: cpuConfig)
            self.decoder = try TransducerDecoder(decoderModel: decoderModel, jointModel: jointModel)
        } catch let error as RuntimeError {
            throw error
        } catch {
            throw RuntimeError.modelFiles("a model would not load")
        }
        self.vocabulary = try Vocabulary(
            path: (directory as NSString).appendingPathComponent(ModelFiles.vocabulary),
            size: decoder.blankId)
        self.mel = MelFeatures(windowSamples: layout.windowSamples, nMels: 128)
        self.spliceSafeTokenIds = WindowMerger.spliceSafeTokenIds(vocabulary: vocabulary.pieces)
        self.caseVariantIds = WindowMerger.caseVariantCanonicalIds(vocabulary: vocabulary.pieces)
    }

    /// Transcribe 16 kHz mono samples of any length with overlapping 15 s
    /// windows. Each window decodes independently; adjacent token streams
    /// are merged on their overlap, then seam duplicates are collapsed.
    func transcribe(_ samples: [Float]) throws -> Transcript {
        var merged: [TokenWindow] = []
        var terms = TermPassState(list: termList)
        for chunkStart in layout.chunkStarts(totalSamples: samples.count) {
            let chunkEnd = min(chunkStart + layout.chunkSamples, samples.count)
            let windowTokens = try autoreleasepool {
                try transcribeWindow(
                    samples: samples, from: chunkStart, count: chunkEnd - chunkStart,
                    globalStart: chunkStart, terms: &terms)
            }
            merged = merge(merged, windowTokens)
        }
        return finished(merged, terms: terms)
    }

    /// The token stream so far with the next window's merged onto it. The
    /// windows of one recording go through here in order, whether they are
    /// decoded in one request or one at a time in a session, so both give
    /// the same stream.
    func merge(_ merged: [TokenWindow], _ windowTokens: [TokenWindow]) -> [TokenWindow] {
        merged.isEmpty
            ? windowTokens
            : WindowMerger().mergeChunks(
                merged,
                windowTokens,
                spliceSafeTokenIds: spliceSafeTokenIds,
                caseVariantIds: caseVariantIds
            )
    }

    /// The transcript of a recording whose every window has been merged,
    /// with what its term pass found.
    func finished(_ windows: [TokenWindow], terms: TermPassState) -> Transcript {
        let merger = WindowMerger()
        var merged = windows
        merged.sort { $0.timestamp < $1.timestamp }
        merged = merger.collapseSeamWordDuplicates(merged, vocabulary: vocabulary.pieces)
        var transcript = Transcript(text: vocabulary.decode(ids: merged.map(\.token)), tokens: merged)
        if let list = terms.list {
            transcript.termList = list
            transcript.detours = TermBias.kept(terms.detours, merged: merged)
            transcript.termPass = terms.stats
        }
        return transcript
    }

    /// Decode one window: `count` samples of `samples` from index `from`,
    /// which is sample `globalStart` of the recording; with a term list,
    /// run the term pass over it into `terms`.
    func transcribeWindow(
        samples: [Float], from localStart: Int, count validCount: Int, globalStart: Int,
        terms: inout TermPassState
    ) throws -> [TokenWindow] {
        var buffer = [Float](repeating: 0, count: layout.windowSamples)
        samples.withUnsafeBufferPointer { src in
            buffer.withUnsafeMutableBufferPointer { dst in
                dst.baseAddress!.update(from: src.baseAddress! + localStart, count: validCount)
            }
        }
        let (melArray, melLength) = try mel.features(window: buffer, validCount: validCount)

        let encoderOutput: MLFeatureProvider
        do {
            encoderOutput = try encoder.prediction(from: EncoderInput(mel: melArray, melLength: melLength))
        } catch {
            throw RuntimeError.decoding("encoder failed")
        }
        guard let encoded = encoderOutput.featureValue(for: "encoder")?.multiArrayValue,
              let encodedLength = encoderOutput.featureValue(for: "encoder_length")?.multiArrayValue,
              encoded.shape.count == 3, encodedLength.count >= 1
        else {
            throw RuntimeError.decoding("encoder gave no output")
        }

        let reportedLength: Int
        switch encodedLength.dataType {
        case .int32:
            reportedLength = Int(encodedLength.withUnsafeBufferPointer(ofType: Int32.self) { $0[0] })
        default:
            reportedLength = encodedLength[0].intValue
        }
        let encoderLength = max(0, min(reportedLength, encoded.shape[2].intValue))
        let trace = terms.list == nil ? nil : GreedyTrace()
        let emissions = try decoder.decode(
            encoded: encoded,
            frameRange: 0..<encoderLength,
            globalFrameOffset: globalStart / layout.frameSamples,
            trace: trace
        )
        if let termList = terms.list, let trace {
            // The second pass reads the same encoder output and reports;
            // the window's tokens above are already final.
            let started = ContinuousClock.now
            terms.detours += try TermBias.search(
                termList, decoder: decoder, encoded: encoded,
                frameRange: 0..<encoderLength,
                globalFrameOffset: globalStart / layout.frameSamples,
                emissions: emissions, trace: trace,
                vocabulary: vocabulary.pieces, stats: &terms.stats)
            terms.stats.seconds += Wire.seconds(started.duration(to: .now))
        }
        return emissions.map { (token: $0.token, timestamp: $0.frame, confidence: $0.prob, duration: 0) }
    }
}

/// Protocol 3: one recording decoded while it is still being captured.
///
/// A window's audio is fixed as soon as the recording reaches its end, and
/// every window decodes alone from a fresh state, so each window that is
/// complete is decoded the moment it is, and merged onto the windows before
/// it exactly as `transcribe` merges them. `finish` decodes what is left
/// (the last window, which may be short) and returns what `transcribe`
/// returns for the whole recording: the same windows, decoded the same way,
/// merged in the same order. Only audio not yet decoded is kept.
///
/// The term list is the one set when the session began, as a request uses
/// the one set when it arrives; the term pass runs over each window as it
/// is decoded, so what it finds and spends is the request's, and `finish`
/// reports it the same way. With no list nothing extra runs.
final class Session {
    private let recognizer: Recognizer
    /// The recording from sample `bufferStart` on.
    private var buffer: [Float] = []
    private var bufferStart = 0
    /// Samples appended in all.
    private(set) var total = 0
    /// Windows decoded and merged so far; window k starts at k strides.
    private(set) var decodedWindows = 0
    private var merged: [TokenWindow] = []
    private var terms: TermPassState
    /// Seconds the term pass spent inside `finish`, at key release.
    private(set) var termPassSecondsAtFinish = 0.0
    /// Why the session can no longer give the recording's transcript.
    private(set) var failure: String?

    init(recognizer: Recognizer) {
        self.recognizer = recognizer
        self.terms = TermPassState(list: recognizer.termList)
    }

    /// Whether the session runs the term pass.
    var hasTerms: Bool { terms.list != nil }

    private var layout: WindowLayout { recognizer.layout }

    /// Add audio, and decode every window it completes. Once the session
    /// has failed the audio is dropped; `finish` reports why.
    func append(_ samples: [Float]) {
        guard failure == nil else { return }
        guard samples.count <= Wire.maximumSamples - total else {
            fail("session longer than the maximum")
            return
        }
        buffer += samples
        total += samples.count
        while failure == nil {
            let start = decodedWindows * layout.strideSamples
            guard start + layout.chunkSamples <= total else { break }
            decodeWindow(at: start, count: layout.chunkSamples)
            // The next window starts one stride on; nothing before it is
            // read again.
            let next = decodedWindows * layout.strideSamples
            if failure == nil {
                buffer.removeFirst(next - bufferStart)
                bufferStart = next
            }
        }
    }

    /// The recording's transcript: the windows `transcribe` would decode
    /// for `total` samples that are not decoded yet, merged on, then
    /// finished as `transcribe` finishes. The session is spent afterwards.
    func finish() throws -> Transcript {
        if let failure {
            throw RuntimeError.decoding(failure)
        }
        guard total > 0 else {
            throw RuntimeError.decoding("empty session")
        }
        let starts = layout.chunkStarts(totalSamples: total)
        guard decodedWindows <= starts.count else {
            throw RuntimeError.decoding("session windows out of step")
        }
        let heldSeconds = terms.stats.seconds
        for start in starts[decodedWindows...] {
            decodeWindow(at: start, count: min(start + layout.chunkSamples, total) - start)
            if let failure {
                throw RuntimeError.decoding(failure)
            }
        }
        termPassSecondsAtFinish = terms.stats.seconds - heldSeconds
        buffer = []
        return recognizer.finished(merged, terms: terms)
    }

    private func decodeWindow(at start: Int, count: Int) {
        // Every window start is a whole number of strides, and the buffer
        // is cut only at the next one.
        guard start == decodedWindows * layout.strideSamples, start >= bufferStart,
              start - bufferStart + count <= buffer.count else {
            fail("session windows out of step")
            return
        }
        do {
            let windowTokens = try autoreleasepool {
                try recognizer.transcribeWindow(
                    samples: buffer, from: start - bufferStart, count: count,
                    globalStart: start, terms: &terms)
            }
            merged = recognizer.merge(merged, windowTokens)
            decodedWindows += 1
        } catch {
            fail("transcription failed: \(error)")
        }
    }

    private func fail(_ reason: String) {
        failure = reason
        buffer = []
        merged = []
    }
}
