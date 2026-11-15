// Protocol 2 word evidence: when each word was heard and how sure the model
// was of it.
//
// `tokenTimings(from:...)` is derived from FluidAudio
// (https://github.com/FluidInference/FluidAudio), tag v0.18.3, commit
// c8b41f98eae668794dd0083bbe028fe7e6585051:
// Sources/FluidAudio/ASR/Parakeet/Unified/UnifiedAsrManager.swift
// (`tokenTimings(from:secondsPerFrame:vocabulary:clipDuration:)`).
// Copyright the FluidAudio authors. Licensed under the Apache License,
// Version 2.0 (native/ParrotSpeechRuntime/LICENSE-Apache-2.0.txt).
// Changed for Whisper Face: it returns this file's own `TokenTiming` and is
// otherwise the same rule.
//
// The word grouping and the confidence rule are Whisper Face's own
// (native/ParrotASRHelper/PROTOCOL.md, "Protocol 2").

import Foundation

struct TokenTiming {
    /// The piece with its word-boundary marker turned into a space.
    let token: String
    let tokenId: Int
    let startTime: Double
    let endTime: Double
    let confidence: Float
}

enum WordEvidence {
    /// About ten minutes at 400 words a minute. A longer list is left out
    /// of the response instead of growing it without bound; the text still
    /// comes back.
    static let maximumWords = 4_096

    /// Emission frames to seconds. RNNT tokens have no duration: each gets a
    /// provisional one-frame end, pulled back to the next token's start when
    /// it would overrun it, and the last is clamped to the clip.
    static func tokenTimings(
        from emissions: [TokenWindow],
        secondsPerFrame: Double,
        vocabulary: [Int: String],
        clipDuration: Double?
    ) -> [TokenTiming] {
        var timings: [TokenTiming] = []
        timings.reserveCapacity(emissions.count)
        for emission in emissions {
            guard let piece = vocabulary[emission.token] else { continue }
            let start = Double(emission.timestamp) * secondsPerFrame
            if let last = timings.indices.last, timings[last].endTime > start {
                let previous = timings[last]
                timings[last] = TokenTiming(
                    token: previous.token, tokenId: previous.tokenId,
                    startTime: previous.startTime, endTime: max(previous.startTime, start),
                    confidence: previous.confidence)
            }
            timings.append(TokenTiming(
                token: piece.replacingOccurrences(of: "\u{2581}", with: " "),
                tokenId: emission.token,
                startTime: start,
                endTime: start + secondsPerFrame,
                confidence: emission.confidence))
        }
        if let clipDuration, let last = timings.indices.last, timings[last].endTime > clipDuration {
            let previous = timings[last]
            timings[last] = TokenTiming(
                token: previous.token, tokenId: previous.tokenId,
                startTime: previous.startTime, endTime: max(previous.startTime, clipDuration),
                confidence: previous.confidence)
        }
        return timings
    }

    /// Add `avg_confidence` and `words` to a protocol 2 response.
    static func add(
        _ transcript: Transcript, sampleCount: Int, vocabulary: [Int: String],
        to response: inout [String: Any]
    ) {
        let timings = tokenTimings(
            from: transcript.tokens,
            secondsPerFrame: secondsPerEncoderFrame,
            vocabulary: vocabulary,
            clipDuration: Double(sampleCount) / 16_000.0)
        let tokens = timings.filter {
            !$0.token.isEmpty && $0.token != "<blank>" && $0.token != "<pad>"
        }
        guard !tokens.isEmpty else { return }
        let total = tokens.reduce(0.0) { $0 + Double($1.confidence) }
        response["avg_confidence"] = rounded(total / Double(tokens.count))
        let words = group(tokens)
        if !words.isEmpty, words.count <= maximumWords {
            response["words"] = words
        }
    }

    /// Group SentencePiece tokens into words on their leading-space
    /// boundaries. A word's `confidence` is the lowest of its tokens that
    /// carry a letter or a digit (sentence punctuation is often decoded less
    /// surely than the word it follows, and must not mark a clearly heard
    /// word as doubtful); a word with none uses all its tokens. The lowest
    /// punctuation-only token, when there is one, is `mark_confidence`.
    static func group(_ tokens: [TokenTiming]) -> [[String: Any]] {
        var words: [[String: Any]] = []
        var text = ""
        var start = 0.0
        var end = 0.0
        var lexical: Double? = nil
        var marks: Double? = nil
        var any = 1.0
        func flush() {
            let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty else { return }
            var word: [String: Any] = [
                "word": trimmed,
                "start": rounded(start),
                "end": rounded(end),
                "confidence": rounded(lexical ?? any),
            ]
            if let marks {
                word["mark_confidence"] = rounded(marks)
            }
            words.append(word)
        }
        for token in tokens {
            if token.token.hasPrefix(" ") || text.isEmpty {
                flush()
                text = ""
                lexical = nil
                marks = nil
                any = 1.0
                start = token.startTime
            }
            text += token.token
            end = token.endTime
            let confidence = Double(token.confidence)
            any = min(any, confidence)
            let visible = token.token.trimmingCharacters(in: .whitespacesAndNewlines)
            if visible.unicodeScalars.contains(where: CharacterSet.alphanumerics.contains) {
                lexical = min(lexical ?? confidence, confidence)
            } else if !visible.isEmpty {
                marks = min(marks ?? confidence, confidence)
            }
        }
        flush()
        return words
    }

    /// Three decimals, as a decimal number: JSONSerialization writes a
    /// Double with seventeen significant digits ("0.95999999999999996").
    static func rounded(_ value: Double) -> NSDecimalNumber {
        guard value.isFinite else { return NSDecimalNumber(value: 0) }
        let thousandths = (value * 1000).rounded()
        return NSDecimalNumber(
            mantissa: UInt64(min(abs(thousandths), 1e15)), exponent: -3,
            isNegative: thousandths < 0)
    }
}
