import Foundation
import FluidAudio

private let sampleRate = 16_000
private let maximumSamples = sampleRate * 10 * 60

@main
struct ParrotASRHelper {
    static func main() async throws {
        let mode = CommandLine.arguments.dropFirst().first ?? "--server"
        let manager = UnifiedAsrManager()
        let started = ContinuousClock.now
        try await manager.loadModels()
        let loadSeconds = seconds(started.duration(to: .now))

        switch mode {
        case "--preload", "--verify":
            emit(["ok": true, "model": "parakeet-unified", "load_s": loadSeconds])
        case "--server":
            emit(["ready": true, "model": "parakeet-unified", "load_s": loadSeconds])
            try await serve(manager)
        default:
            FileHandle.standardError.write(
                Data("usage: parrot-asr-helper [--server|--preload|--verify]\n".utf8)
            )
            exit(2)
        }
    }

    private static func serve(_ manager: UnifiedAsrManager) async throws {
        let input = FileHandle.standardInput
        while let header = try readExactly(input, count: 8) {
            var rawCount: UInt64 = 0
            _ = withUnsafeMutableBytes(of: &rawCount) {
                header.copyBytes(to: $0)
            }
            let count = Int(UInt64(littleEndian: rawCount))
            guard count > 0, count <= maximumSamples else {
                emit(["ok": false, "error": "invalid sample count"])
                continue
            }
            guard let payload = try readExactly(input, count: count * 4) else {
                throw HelperError.truncatedAudio
            }
            var samples = [Float](repeating: 0, count: count)
            _ = samples.withUnsafeMutableBytes { payload.copyBytes(to: $0) }
            let started = ContinuousClock.now
            do {
                let text = try await manager.transcribe(samples)
                emit([
                    "ok": true,
                    "text": text,
                    "processing_s": seconds(started.duration(to: .now)),
                ])
            } catch {
                emit(["ok": false, "error": String(describing: error)])
            }
        }
    }

    private static func readExactly(
        _ handle: FileHandle, count: Int
    ) throws -> Data? {
        var result = Data()
        while result.count < count {
            guard let part = try handle.read(upToCount: count - result.count),
                  !part.isEmpty else {
                return result.isEmpty ? nil : result
            }
            result.append(part)
        }
        return result
    }

    private static func emit(_ value: [String: Any]) {
        guard let data = try? JSONSerialization.data(
            withJSONObject: value, options: [.sortedKeys]
        ) else { return }
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    }

    private static func seconds(_ duration: Duration) -> Double {
        let components = duration.components
        return Double(components.seconds)
            + Double(components.attoseconds) / 1_000_000_000_000_000_000
    }
}

private enum HelperError: Error {
    case truncatedAudio
}
