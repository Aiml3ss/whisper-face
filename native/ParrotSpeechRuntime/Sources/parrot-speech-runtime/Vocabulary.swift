// Token ids to text.
//
// Derived from FluidAudio (https://github.com/FluidInference/FluidAudio),
// tag v0.15.5, commit 19600a485baa4998812e4654b70d2bab8f2c9949:
// Sources/FluidAudio/ASR/Parakeet/Streaming/Tokenizer.swift. Copyright the
// FluidAudio authors. Licensed under the Apache License, Version 2.0
// (native/ParrotSpeechRuntime/LICENSE-Apache-2.0.txt).
//
// Changed for Whisper Face: the file is read by path, a malformed vocabulary
// or one whose size does not match the model is an error instead of a crash
// or empty transcripts; unused accessors are removed.

import Foundation

/// The model's SentencePiece vocabulary (`vocab.json`: id -> piece).
final class Vocabulary {
    /// id -> piece, exactly as the file has it.
    let pieces: [Int: String]

    /// `size` is the number of non-blank tokens the model emits (its blank
    /// id): the file must name each id below it exactly once. A vocabulary
    /// of another shape would load and then decode every request to empty
    /// text, so it fails the start instead.
    init(path: String, size: Int) throws {
        guard let data = FileManager.default.contents(atPath: path),
              let json = try? JSONSerialization.jsonObject(with: data, options: []),
              let table = json as? [String: String] else {
            throw RuntimeError.modelFiles("vocabulary is missing or malformed")
        }
        var pieces: [Int: String] = [:]
        for (key, value) in table {
            guard let id = Int(key), id >= 0, id < size else {
                throw RuntimeError.modelFiles("vocabulary does not match the model")
            }
            pieces[id] = value
        }
        guard pieces.count == size else {
            throw RuntimeError.modelFiles("vocabulary does not match the model")
        }
        self.pieces = pieces
    }

    /// Concatenate the pieces, turn word-boundary markers into spaces and
    /// trim the ends.
    func decode(ids: [Int]) -> String {
        var text = ""
        for id in ids {
            if let token = pieces[id] {
                text += token
            }
        }
        return text.replacingOccurrences(of: "\u{2581}", with: " ")
            .trimmingCharacters(in: .whitespaces)
    }
}
