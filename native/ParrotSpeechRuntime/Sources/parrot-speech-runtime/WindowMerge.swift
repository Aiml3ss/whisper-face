// Joining the token streams of overlapping 15 s windows.
//
// Derived from FluidAudio (https://github.com/FluidInference/FluidAudio),
// tag v0.15.5, commit 19600a485baa4998812e4654b70d2bab8f2c9949:
// Sources/FluidAudio/ASR/Parakeet/SlidingWindow/TDT/ChunkProcessor.swift
// (mergeChunks and the functions it calls, spliceSafeTokenIds,
// caseVariantCanonicalIds, collapseSeamWordDuplicates, isSpliceSafePiece),
// Sources/FluidAudio/ASR/Parakeet/TokenDeduplication/SequenceMatcher.swift
// (findContiguousMatches, findLongestCommonSubsequence) and
// Sources/FluidAudio/ASR/Parakeet/SlidingWindow/CustomVocabulary/Rescorer/
// VocabularyRescorer+Utilities.swift (isWordBoundary,
// stripWordBoundaryPrefix). Copyright the FluidAudio authors. Licensed under
// the Apache License, Version 2.0
// (native/ParrotSpeechRuntime/LICENSE-Apache-2.0.txt).
//
// Changed for Whisper Face: only the merge used by the Parakeet Unified
// offline path is kept (no TDT chunk scheduling, worker pool, silence
// alignment or audio source), the matcher is specialised to one element
// type, and names are local to this file. The matching and splicing rules,
// and the order in which they are tried, are unchanged, so long dictations
// merge exactly as the FluidAudio 0.15.5 helper merges them.

import Foundation

/// One emitted token: its id, the global encoder frame it was emitted at,
/// its softmax probability, and a duration the Unified path leaves at 0.
typealias TokenWindow = (token: Int, timestamp: Int, confidence: Float, duration: Int)

/// SentencePiece word-boundary marker (U+2581).
let wordBoundaryMarker = "\u{2581}"

/// Seconds per encoder frame (1280 samples at 16 kHz).
let secondsPerEncoderFrame: Double = 1280.0 / 16_000.0

func isWordBoundary(_ token: String) -> Bool {
    token.hasPrefix(wordBoundaryMarker) || token.hasPrefix(" ")
}

func stripWordBoundaryPrefix(_ token: String) -> String {
    if token.hasPrefix(wordBoundaryMarker) || token.hasPrefix(" ") {
        return String(token.dropFirst())
    }
    return token
}

struct WindowMerger {
    /// The overlap between adjacent windows, in seconds.
    let overlapSeconds: Double = 2.0

    private struct IndexedToken {
        let index: Int
        let token: TokenWindow
        let start: Double
        let end: Double
    }

    private struct Match {
        let leftStartIndex: Int
        let rightStartIndex: Int
    }

    /// Ids whose piece may start the part spliced in from the right window:
    /// word-initial pieces and punctuation-only pieces. nil for an empty
    /// vocabulary, which leaves the merge unchanged.
    static func spliceSafeTokenIds(vocabulary: [Int: String]) -> Set<Int>? {
        guard !vocabulary.isEmpty else { return nil }
        var ids = Set<Int>()
        for (id, piece) in vocabulary where isSpliceSafePiece(piece) {
            ids.insert(id)
        }
        return ids
    }

    /// Maps every id that has a case-only twin to one shared id (the
    /// all-lower-case one when there is one), so `▁Meeting` and `▁meeting`
    /// match at a seam. nil when no piece has a twin.
    static func caseVariantCanonicalIds(vocabulary: [Int: String]) -> [Int: Int]? {
        guard !vocabulary.isEmpty else { return nil }
        var groups: [String: [Int]] = [:]
        for (id, piece) in vocabulary {
            groups[piece.lowercased(), default: []].append(id)
        }
        var canon: [Int: Int] = [:]
        for (folded, ids) in groups where ids.count > 1 {
            let canonical = ids.first { vocabulary[$0] == folded } ?? ids.min()!
            for id in ids { canon[id] = canonical }
        }
        return canon.isEmpty ? nil : canon
    }

    /// Drop an adjacent case-only duplicate of a word left by a window that
    /// re-heard the seam word as a sentence start ("we don't have Have a").
    /// The pair collapses only when the words differ only in case, start
    /// within the overlap, and the first does not end a sentence; the
    /// lower-case copy is kept, else the earlier one.
    func collapseSeamWordDuplicates(
        _ tokens: [TokenWindow],
        vocabulary: [Int: String]
    ) -> [TokenWindow] {
        guard !vocabulary.isEmpty, tokens.count > 1 else { return tokens }
        let overlapFrames = Int((overlapSeconds / secondsPerEncoderFrame).rounded())

        func piece(_ id: Int) -> String { vocabulary[id] ?? "" }
        func startsWord(_ id: Int) -> Bool {
            let p = piece(id)
            return p.hasPrefix(wordBoundaryMarker) || p.hasPrefix(" ")
        }

        struct Word {
            var tokens: [TokenWindow]
            var core: String
            var startTimestamp: Int
            var endsSentence: Bool
        }

        var words: [Word] = []
        for token in tokens {
            if words.isEmpty || startsWord(token.token) {
                words.append(Word(tokens: [token], core: "", startTimestamp: token.timestamp, endsSentence: false))
            } else {
                words[words.count - 1].tokens.append(token)
            }
        }

        let strippable = CharacterSet.punctuationCharacters.union(.whitespaces)
        for index in words.indices {
            var text = ""
            for token in words[index].tokens {
                text += stripWordBoundaryPrefix(piece(token.token))
            }
            words[index].core = text.trimmingCharacters(in: strippable)
            if let last = text.last { words[index].endsSentence = ".?!:".contains(last) }
        }

        var keep = [Bool](repeating: true, count: words.count)
        var lastKept = -1
        for index in words.indices {
            guard lastKept >= 0 else {
                lastKept = index
                continue
            }
            let previous = words[lastKept]
            let current = words[index]
            let previousCore = previous.core
            let currentCore = current.core

            let isSeamDuplicate =
                !previousCore.isEmpty && !currentCore.isEmpty
                && previousCore != currentCore
                && previousCore.lowercased() == currentCore.lowercased()
                && currentCore.first?.isLetter == true
                && !previous.endsSentence
                && current.startTimestamp - previous.startTimestamp <= overlapFrames

            guard isSeamDuplicate else {
                lastKept = index
                continue
            }

            if currentCore == currentCore.lowercased(), previousCore != previousCore.lowercased() {
                keep[lastKept] = false
                lastKept = index
            } else {
                keep[index] = false
            }
        }

        var result: [TokenWindow] = []
        result.reserveCapacity(tokens.count)
        for index in words.indices where keep[index] {
            result.append(contentsOf: words[index].tokens)
        }
        return result
    }

    /// A piece is splice-safe when decoding it right after another word does
    /// not glue two words together: it starts a word, or is all punctuation
    /// and symbols.
    static func isSpliceSafePiece(_ piece: String) -> Bool {
        guard !piece.isEmpty else { return false }
        if isWordBoundary(piece) { return true }
        return piece.unicodeScalars.allSatisfy { scalar in
            CharacterSet.punctuationCharacters.contains(scalar)
                || CharacterSet.symbols.contains(scalar)
        }
    }

    /// Merge the right window's tokens into the left's across their overlap.
    func mergeChunks(
        _ left: [TokenWindow],
        _ right: [TokenWindow],
        spliceSafeTokenIds: Set<Int>? = nil,
        caseVariantIds: [Int: Int]? = nil
    ) -> [TokenWindow] {
        if left.isEmpty { return right }
        if right.isEmpty { return left }

        let frameDuration = secondsPerEncoderFrame
        let overlapDuration = overlapSeconds
        let halfOverlapWindow = overlapDuration / 2

        func startTime(of token: TokenWindow) -> Double {
            Double(token.timestamp) * frameDuration
        }

        func endTime(of token: TokenWindow) -> Double {
            startTime(of: token) + frameDuration
        }

        let leftEndTime = endTime(of: left.last!)
        let rightStartTime = startTime(of: right.first!)

        if leftEndTime <= rightStartTime {
            return left + right
        }

        let overlapLeft: [IndexedToken] = left.enumerated().compactMap { offset, token in
            let start = startTime(of: token)
            let end = start + frameDuration
            guard end > rightStartTime - overlapDuration else { return nil }
            return IndexedToken(index: offset, token: token, start: start, end: end)
        }

        let overlapRight: [IndexedToken] = right.enumerated().compactMap { offset, token in
            let start = startTime(of: token)
            guard start < leftEndTime + overlapDuration else { return nil }
            return IndexedToken(index: offset, token: token, start: start, end: start + frameDuration)
        }

        guard overlapLeft.count >= 2 && overlapRight.count >= 2 else {
            return mergeByMidpoint(
                left: left, right: right, leftEndTime: leftEndTime, rightStartTime: rightStartTime,
                frameDuration: frameDuration, spliceSafeTokenIds: spliceSafeTokenIds)
        }

        let minimumPairs = max(overlapLeft.count / 2, 1)

        let timeTolerantMatcher: (IndexedToken, IndexedToken) -> Bool = { l, r in
            Self.tokensMatch(l, r, tolerance: halfOverlapWindow, caseVariantIds: caseVariantIds)
        }

        let contiguousMatches = Self.findContiguousMatches(
            left: overlapLeft, right: overlapRight, matcher: timeTolerantMatcher)
        let contiguousPairs = contiguousMatches.map { ($0.leftStartIndex, $0.rightStartIndex) }

        if contiguousPairs.count >= minimumPairs {
            return mergeUsingMatches(
                matches: contiguousPairs,
                overlapLeft: overlapLeft,
                overlapRight: overlapRight,
                left: left,
                right: right,
                spliceSafeTokenIds: spliceSafeTokenIds
            )
        }

        let lcsMatches = Self.findLongestCommonSubsequence(
            left: overlapLeft, right: overlapRight, matcher: timeTolerantMatcher)

        guard !lcsMatches.isEmpty else {
            return mergeByMidpoint(
                left: left, right: right, leftEndTime: leftEndTime, rightStartTime: rightStartTime,
                frameDuration: frameDuration, spliceSafeTokenIds: spliceSafeTokenIds)
        }

        let lcsPairs = lcsMatches.map { ($0.leftStartIndex, $0.rightStartIndex) }
        return mergeUsingMatches(
            matches: lcsPairs,
            overlapLeft: overlapLeft,
            overlapRight: overlapRight,
            left: left,
            right: right,
            spliceSafeTokenIds: spliceSafeTokenIds
        )
    }

    private static func tokensMatch(
        _ left: IndexedToken,
        _ right: IndexedToken,
        tolerance: Double,
        caseVariantIds: [Int: Int]?
    ) -> Bool {
        guard tokenIdsMatch(left.token.token, right.token.token, caseVariantIds: caseVariantIds) else {
            return false
        }
        let timeDifference = abs(left.start - right.start)
        return timeDifference < tolerance
    }

    private static func tokenIdsMatch(_ left: Int, _ right: Int, caseVariantIds: [Int: Int]?) -> Bool {
        if left == right { return true }
        guard let caseVariantIds, let lhs = caseVariantIds[left], let rhs = caseVariantIds[right] else {
            return false
        }
        return lhs == rhs
    }

    private func mergeUsingMatches(
        matches: [(Int, Int)],
        overlapLeft: [IndexedToken],
        overlapRight: [IndexedToken],
        left: [TokenWindow],
        right: [TokenWindow],
        spliceSafeTokenIds: Set<Int>?
    ) -> [TokenWindow] {
        let leftIndices = matches.map { overlapLeft[$0.0].index }
        let rightIndices = matches.map { overlapRight[$0.1].index }

        var result: [TokenWindow] = []

        if let firstLeft = leftIndices.first, firstLeft > 0 {
            result.append(contentsOf: left[..<firstLeft])
        }

        for idx in 0..<matches.count {
            let leftIndex = leftIndices[idx]
            let rightIndex = rightIndices[idx]

            result.append(left[leftIndex])

            guard idx < matches.count - 1 else { continue }

            let nextLeftIndex = leftIndices[idx + 1]
            let nextRightIndex = rightIndices[idx + 1]

            let gapLeft = nextLeftIndex > leftIndex + 1 ? Array(left[(leftIndex + 1)..<nextLeftIndex]) : []
            let gapRight = nextRightIndex > rightIndex + 1 ? Array(right[(rightIndex + 1)..<nextRightIndex]) : []

            if gapRight.count > gapLeft.count {
                result.append(contentsOf: gapRight)
            } else {
                result.append(contentsOf: gapLeft)
            }
        }

        if let lastRight = rightIndices.last, lastRight + 1 < right.count {
            let tail = right[(lastRight + 1)...]
            if let safeIds = spliceSafeTokenIds,
                let firstTail = tail.first,
                !safeIds.contains(firstTail.token)
            {
                // The splice would land mid-word: re-splice at a word
                // boundary so exactly one window segments the seam word.
                if let wordStart = wordInitialIndex(in: right, endingAt: lastRight, safeIds: safeIds),
                    popSeamWord(from: &result, safeIds: safeIds)
                {
                    result.append(contentsOf: right[wordStart...])
                } else {
                    if let lastLeft = leftIndices.last {
                        var cursor = lastLeft + 1
                        while cursor < left.count, !safeIds.contains(left[cursor].token) {
                            result.append(left[cursor])
                            cursor += 1
                        }
                    }
                    if let resume = tail.firstIndex(where: { safeIds.contains($0.token) }) {
                        result.append(contentsOf: tail[resume...])
                    } else {
                        result.append(contentsOf: tail)
                    }
                }
            } else {
                result.append(contentsOf: tail)
            }
        }

        return result
    }

    /// Index of the word-initial (or punctuation) piece starting the word
    /// that contains `anchor`, or nil when the stream begins mid-word.
    private func wordInitialIndex(
        in stream: [TokenWindow],
        endingAt anchor: Int,
        safeIds: Set<Int>
    ) -> Int? {
        var index = anchor
        while index >= 0 {
            if safeIds.contains(stream[index].token) { return index }
            index -= 1
        }
        return nil
    }

    /// Remove the trailing seam word from `result`; false, leaving it
    /// untouched, when it holds no word-initial piece at all.
    private func popSeamWord(from result: inout [TokenWindow], safeIds: Set<Int>) -> Bool {
        var cursor = result.count - 1
        while cursor >= 0 {
            if safeIds.contains(result[cursor].token) {
                result.removeLast(result.count - cursor)
                return true
            }
            cursor -= 1
        }
        return false
    }

    private func mergeByMidpoint(
        left: [TokenWindow],
        right: [TokenWindow],
        leftEndTime: Double,
        rightStartTime: Double,
        frameDuration: Double,
        spliceSafeTokenIds: Set<Int>?
    ) -> [TokenWindow] {
        let cutoff = (leftEndTime + rightStartTime) / 2
        var leftEnd = left.firstIndex { Double($0.timestamp) * frameDuration >= cutoff } ?? left.count
        var rightStart = right.firstIndex { Double($0.timestamp) * frameDuration >= cutoff } ?? right.count
        if let safeIds = spliceSafeTokenIds {
            if leftEnd > 0 {
                while leftEnd < left.count, !safeIds.contains(left[leftEnd].token) {
                    leftEnd += 1
                }
            }
            var scanIndex = rightStart
            while scanIndex < right.count, !safeIds.contains(right[scanIndex].token) {
                scanIndex += 1
            }
            if scanIndex < right.count {
                rightStart = scanIndex
            }
        }
        return Array(left[..<leftEnd]) + Array(right[rightStart...])
    }

    // MARK: - Sequence matching

    /// The longest run of consecutive matches, as one match per element.
    private static func findContiguousMatches(
        left: [IndexedToken],
        right: [IndexedToken],
        matcher: (IndexedToken, IndexedToken) -> Bool
    ) -> [Match] {
        var best: [Match] = []
        for i in 0..<left.count {
            for j in 0..<right.count {
                if matcher(left[i], right[j]) {
                    var current: [Match] = []
                    var k = i
                    var l = j
                    while k < left.count && l < right.count {
                        if matcher(left[k], right[l]) {
                            current.append(Match(leftStartIndex: k, rightStartIndex: l))
                            k += 1
                            l += 1
                        } else {
                            break
                        }
                    }
                    if current.count > best.count {
                        best = current
                    }
                }
            }
        }
        return best
    }

    /// Longest common subsequence, as one match per matched element, in
    /// order.
    private static func findLongestCommonSubsequence(
        left: [IndexedToken],
        right: [IndexedToken],
        matcher: (IndexedToken, IndexedToken) -> Bool
    ) -> [Match] {
        let leftCount = left.count
        let rightCount = right.count
        var dp = Array(repeating: Array(repeating: 0, count: rightCount + 1), count: leftCount + 1)
        for i in 1...leftCount {
            for j in 1...rightCount {
                if matcher(left[i - 1], right[j - 1]) {
                    dp[i][j] = dp[i - 1][j - 1] + 1
                } else {
                    dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
                }
            }
        }
        var matches: [Match] = []
        var i = leftCount
        var j = rightCount
        while i > 0 && j > 0 {
            if matcher(left[i - 1], right[j - 1]) {
                matches.append(Match(leftStartIndex: i - 1, rightStartIndex: j - 1))
                i -= 1
                j -= 1
            } else if dp[i - 1][j] > dp[i][j - 1] {
                i -= 1
            } else {
                j -= 1
            }
        }
        return matches.reversed()
    }
}
