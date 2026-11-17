// The helper wire protocol shared with native/ParrotASRHelper: arguments,
// framed requests on stdin, one JSON line per answer on stdout
// (native/ParrotASRHelper/PROTOCOL.md).

import Foundation

enum Wire {
    static let sampleRate = 16_000
    /// Ten minutes of audio, as for the FluidAudio helper.
    static let maximumSamples = sampleRate * 10 * 60
    /// The newest protocol this runtime speaks. Without `--protocol N` it
    /// answers exactly as protocol 1 does.
    static let supportedProtocol = 3

    /// Protocol 3 sessions. A request header is a little-endian UInt64: a
    /// batch request's is the sample count, which is never above
    /// `maximumSamples`, so its top byte is 0. A session operation puts its
    /// code in the top byte and a count in the rest. Below protocol 3 such
    /// a header is only an invalid sample count, as it always was.
    enum Operation: UInt64 {
        case batch = 0
        /// Start a session (count 0), dropping any unfinished one. No answer.
        case begin = 1
        /// `count` samples follow, added to the session. No answer.
        case append = 2
        /// Count 0. Answers as a batch request does for the session's
        /// recording, plus `samples`, and ends the session.
        case finish = 3
        /// Count 0. Drops the session. No answer.
        case cancel = 4
    }

    static let operationShift: UInt64 = 56
    static let countMask: UInt64 = (1 << 56) - 1
    /// Reported as `engine` in a protocol 2 status line.
    static let engine = "parrot-speech-runtime-1"
    static let model = "parakeet-unified"
    /// The pinned model's folder name under FluidAudio's default models
    /// folder, which is where Python puts it (`PARAKEET_MODEL_DIR`).
    static let modelFolderName = "parakeet-unified-en-0.6b"
    static let usage =
        "usage: parrot-speech-runtime [--server|--preload|--verify] [--protocol N] [--models DIR]\n"

    /// `--protocol N`, capped at what this runtime speaks. Missing or
    /// unreadable is protocol 1; unknown arguments are ignored, as the
    /// protocol 1 helper ignores everything after its mode.
    static func negotiatedProtocol(_ arguments: [String]) -> Int {
        guard let flag = arguments.firstIndex(of: "--protocol"),
              arguments.indices.contains(flag + 1),
              let requested = Int(arguments[flag + 1]) else { return 1 }
        return max(1, min(requested, supportedProtocol))
    }

    /// The value after `flag`, if there is a non-empty one.
    static func value(after flag: String, in arguments: [String]) -> String? {
        guard let index = arguments.firstIndex(of: flag),
              arguments.indices.contains(index + 1),
              !arguments[index + 1].isEmpty else { return nil }
        return arguments[index + 1]
    }

    /// `--models DIR`, else the folder FluidAudio's helper loads by default
    /// (the same folder).
    static func modelDirectory(_ arguments: [String]) -> String {
        if let directory = value(after: "--models", in: arguments) {
            return directory
        }
        let support = FileManager.default.urls(
            for: .applicationSupportDirectory, in: .userDomainMask
        ).first?.path ?? (NSHomeDirectory() as NSString)
            .appendingPathComponent("Library/Application Support")
        return ((support as NSString)
            .appendingPathComponent("FluidAudio/Models") as NSString)
            .appendingPathComponent(modelFolderName)
    }

    /// Fill `buffer` from a file descriptor (stdin is 0), straight into its
    /// memory. Returns the number of bytes read: all of them, fewer when the
    /// input ended part way (0 at a clean end), or -1 on a read error.
    static func readFully(fileDescriptor: Int32, into buffer: UnsafeMutableRawBufferPointer) -> Int {
        guard let base = buffer.baseAddress else { return 0 }
        var filled = 0
        while filled < buffer.count {
            let result = read(fileDescriptor, base + filled, buffer.count - filled)
            if result > 0 {
                filled += result
            } else if result == 0 {
                break
            } else if errno == EINTR {
                continue
            } else {
                return -1
            }
        }
        return filled
    }

    /// One JSON object, keys sorted, on one line.
    static func emit(_ value: [String: Any]) {
        guard let data = try? JSONSerialization.data(
            withJSONObject: value, options: [.sortedKeys]
        ) else { return }
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    }

    static func seconds(_ duration: Duration) -> Double {
        let components = duration.components
        return Double(components.seconds)
            + Double(components.attoseconds) / 1_000_000_000_000_000_000
    }
}
