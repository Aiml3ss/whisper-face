// The term pass: where the user's own terms could have been heard.
//
// Whisper Face's own. After the normal decode of a window, at each word the
// normal pass began, the pass asks whether one of the user's terms could
// have been emitted there instead. It starts from the decoder state the
// normal pass held at that word, replays the same encoder output, and finds
// by dynamic programming over (frame, term-trie node) the cheapest way to
// emit a whole term, where every step costs the joint network's logit gap
// between its own first choice and the action taken (a term piece, or the
// blank that moves to the next frame). A piece or blank outside the model's
// 64 candidates at that step cannot be taken, and the summed gap ("debt")
// may not pass the budget the app sent. A term also has to end where a word
// can end: closing it costs the gap of the blank or of a word-starting
// piece after its last piece.
//
// The pass reports what it found ("detours") and changes nothing. The
// response's text and words are the normal pass's, always; only the app
// decides what to do with a detour (voice_compiler.apply_vocabulary_evidence).
// With no terms, or a budget of zero, the pass does not run and a response
// is exactly what it was without it.

import CoreML
import Foundation

/// The user's terms as paths of the model's vocabulary pieces, in a trie.
final class TermList {
    struct Node {
        /// piece id -> child node index. A child's index is always larger
        /// than its parent's.
        var children: [Int: Int] = [:]
        var parent = -1
        var piece = -1
        /// Terms that end at this node.
        var terms: [Int] = []
    }

    static let maximumTerms = 200
    static let maximumTermCharacters = 64
    /// Spellings tried per term: as written, lower case, first letter
    /// capitalised; each split into at most this many piece sequences of
    /// the fewest pieces (or one more).
    static let segmentationsPerSpelling = 3
    /// A budget the app may send is clamped to this.
    static let maximumDebt: Float = 20

    let version: Int
    let terms: [String]
    let maxDebt: Float
    let dropped: Int
    let nodes: [Node]
    /// The most words any term has.
    let maxTermWords: Int

    /// Nil when nothing is left to look for: no usable term, or no budget.
    init?(version: Int, terms raw: [String], maxDebt: Double, vocabulary: [Int: String]) {
        var pieceIds: [String: Int] = [:]
        for (id, piece) in vocabulary { pieceIds[piece] = id }
        var kept: [String] = []
        var seen = Set<String>()
        var dropped = 0
        for value in raw {
            let text = value.split(whereSeparator: \.isWhitespace).joined(separator: " ")
            guard !text.isEmpty, text.count <= Self.maximumTermCharacters,
                  text.unicodeScalars.contains(where: CharacterSet.letters.contains),
                  kept.count < Self.maximumTerms,
                  seen.insert(text.lowercased()).inserted else {
                dropped += 1
                continue
            }
            kept.append(text)
        }
        self.version = version
        self.maxDebt = Float(max(0, min(maxDebt, Double(Self.maximumDebt))))
        var usable: [String] = []
        var nodes = [Node()]
        var words = 0
        for text in kept {
            var spellings = [text, text.lowercased()]
            spellings.append(text.prefix(1).uppercased() + text.dropFirst())
            var paths: [[Int]] = []
            for spelling in spellings {
                for path in Self.segmentations(spelling, pieceIds: pieceIds,
                                               limit: Self.segmentationsPerSpelling)
                where !paths.contains(path) {
                    paths.append(path)
                }
            }
            guard !paths.isEmpty else {
                dropped += 1
                continue
            }
            let index = usable.count
            usable.append(text)
            words = max(words, text.split(separator: " ").count)
            for path in paths { Self.insert(path, term: index, into: &nodes) }
        }
        self.terms = usable
        self.nodes = nodes
        self.dropped = dropped
        self.maxTermWords = words
        if usable.isEmpty || self.maxDebt <= 0 { return nil }
    }

    private static func insert(_ path: [Int], term: Int, into nodes: inout [Node]) {
        var node = 0
        for piece in path {
            if let child = nodes[node].children[piece] {
                node = child
            } else {
                var child = Node()
                child.parent = node
                child.piece = piece
                nodes.append(child)
                nodes[node].children[piece] = nodes.count - 1
                node = nodes.count - 1
            }
        }
        if !nodes[node].terms.contains(term) { nodes[node].terms.append(term) }
    }

    /// Up to `limit` ways to write "▁" + text (spaces as "▁") in the
    /// vocabulary's pieces, fewest pieces first and at most one more than
    /// the fewest. The first piece carries a letter: a bare "▁" may start
    /// only a term no word-start piece begins.
    static func segmentations(_ text: String, pieceIds: [String: Int], limit: Int) -> [[Int]] {
        let marker: Character = "\u{2581}"
        let characters = Array(String(marker) + text.replacingOccurrences(of: " ", with: String(marker)))
        let count = characters.count
        let longest = 16
        func piece(_ start: Int, _ end: Int) -> Int? {
            pieceIds[String(characters[start..<end])]
        }
        for allowBare in [false, true] {
            let unreachable = Int.max
            var fewest = [Int](repeating: unreachable, count: count + 1)
            fewest[count] = 0
            for start in stride(from: count - 1, through: 0, by: -1) {
                for end in (start + 1)...min(count, start + longest)
                where fewest[end] != unreachable && piece(start, end) != nil {
                    if start == 0 && end == 1 && !allowBare { continue }
                    fewest[start] = min(fewest[start], fewest[end] + 1)
                }
            }
            guard fewest[0] != unreachable else { continue }
            let budget = fewest[0] + 1
            var found: [[Int]] = []
            var path: [Int] = []
            func walk(_ start: Int) {
                if found.count >= limit { return }
                if start == count {
                    found.append(path)
                    return
                }
                for end in stride(from: min(count, start + longest), to: start, by: -1) {
                    guard fewest[end] != unreachable, let id = piece(start, end) else { continue }
                    if start == 0 && end == 1 && !allowBare { continue }
                    if path.count + 1 + fewest[end] > budget { continue }
                    path.append(id)
                    walk(end)
                    path.removeLast()
                }
            }
            walk(0)
            return found.sorted { $0.count < $1.count }
        }
        return []
    }
}

/// One way a term could have been emitted where the normal pass heard
/// other words.
struct Detour {
    struct Step {
        /// Global encoder frame.
        let frame: Int
        let piece: Int
        /// The piece's rank among that step's 64 candidates.
        let rank: Int
        /// The joint network's first logit minus this piece's.
        let gap: Float
    }

    let term: Int
    let steps: [Step]
    let debt: Float
    /// The largest single gap paid: a piece, a blank, or the close.
    let maxStepGap: Float
    /// The normal pass's tokens this would replace, with their frames.
    let heard: [(token: Int, frame: Int)]
    /// The global frame of each replaced word's first token.
    let heardWordStarts: [Int]
    /// The normal pass's lowest token probability in those words.
    let heardMinProbability: Float
}

/// What the pass did for one request.
struct TermPassStats {
    var forks = 0
    var jointCalls = 0
    var decoderCalls = 0
    /// The pass stopped at its call limit.
    var stopped = false
    var seconds = 0.0
}

enum TermBias {
    /// The most joint and decoder calls one request may spend: a list that
    /// would cost more stops the pass, which says so, rather than delay a
    /// dictation.
    static let maximumCalls = 1_500
    /// Detours reported per request, lowest debt first.
    static let maximumDetours = 64
    /// Frames past the start of the word after the longest term's span.
    static let slackFrames = 2

    /// Search one decoded window. `emissions` are the window's own tokens
    /// (global frames); `trace` is what the normal pass recorded.
    static func search(
        _ list: TermList,
        decoder: TransducerDecoder,
        encoded: MLMultiArray,
        frameRange: Range<Int>,
        globalFrameOffset offset: Int,
        emissions: [TransducerDecoder.Emission],
        trace: GreedyTrace,
        vocabulary: [Int: String],
        stats: inout TermPassStats
    ) throws -> [Detour] {
        guard !emissions.isEmpty, !frameRange.isEmpty else { return [] }
        let marker = "\u{2581}"
        var wordStarts: [Int] = []
        for (index, emission) in emissions.enumerated()
        where index == 0 || (vocabulary[emission.token] ?? "").hasPrefix(marker) {
            wordStarts.append(index)
        }
        // Steps grouped by how many tokens had been emitted before them.
        var stepsFrom: [Int: [GreedyTrace.Step]] = [:]
        for step in trace.steps { stepsFrom[step.emitted, default: []].append(step) }
        let firstPieces = Set(list.nodes[0].children.keys)
        let wordStartPieces = Set(vocabulary.compactMap { $0.value.hasPrefix(marker) ? $0.key : nil })
        let scratch = try MLMultiArray(
            shape: [1, NSNumber(value: encoded.shape[1].intValue), 1], dataType: .float32)
        var detours: [Detour] = []

        for (wordIndex, start) in wordStarts.enumerated() {
            guard let observed = stepsFrom[start], let first = observed.first,
                  trace.states.indices.contains(start) else { continue }
            // Fork only where a term's first piece was among the model's
            // candidates within the budget.
            let triggered = observed.contains { step in
                guard let top = step.candidates.logits.first else { return false }
                return step.candidates.ids.indices.contains {
                    firstPieces.contains(step.candidates.ids[$0])
                        && top - step.candidates.logits[$0] <= list.maxDebt
                }
            }
            guard triggered else { continue }
            if stats.stopped { break }
            stats.forks += 1

            let startT = first.t
            let lastT = frameRange.upperBound - 1
            let after = wordIndex + list.maxTermWords + 1
            let endT = after < wordStarts.count
                ? min(lastT, emissions[wordStarts[after]].frame - offset + slackFrames)
                : lastT
            guard startT <= endT else { continue }

            var rootJoint: [Int: JointCandidates] = [:]
            for step in observed where rootJoint[step.t] == nil { rootJoint[step.t] = step.candidates }
            var states: [Int: TransducerDecoder.DecoderStep] = [0: trace.states[start]]
            var joints: [Int: JointCandidates] = [:]
            func state(_ node: Int) throws -> TransducerDecoder.DecoderStep {
                if let known = states[node] { return known }
                let parent = try state(list.nodes[node].parent)
                stats.decoderCalls += 1
                let next = try decoder.advance(list.nodes[node].piece, from: parent)
                states[node] = next
                return next
            }
            let nodeCount = list.nodes.count
            func key(_ t: Int, _ node: Int) -> Int { (t - startT) * nodeCount + node }
            func joint(_ t: Int, _ node: Int) throws -> JointCandidates? {
                if node == 0, let seen = rootJoint[t] { return seen }
                if let known = joints[key(t, node)] { return known }
                guard stats.jointCalls + stats.decoderCalls < maximumCalls else {
                    stats.stopped = true
                    return nil
                }
                let parentState = try state(node)
                stats.jointCalls += 1
                let found = try decoder.jointCandidates(
                    encoded: encoded, t: t, state: parentState, scratch: scratch)
                joints[key(t, node)] = found
                return found
            }

            // back[key] = (previous key, piece or -1 for a blank, rank, gap)
            var back: [Int: (previous: Int, piece: Int, rank: Int, gap: Float)] = [:]
            var best: [Int: (debt: Float, key: Int, close: Float)] = [:]
            var current: [Int: Float] = [0: 0]
            for t in startT...endT {
                var next: [Int: Float] = [:]
                // Ascending node order: a child's index is above its
                // parent's, so each node's cost is final when it is taken.
                var pending = current.keys.sorted()
                while !pending.isEmpty {
                    let node = pending.removeFirst()
                    guard let cost = current[node], cost <= list.maxDebt,
                          let candidates = try joint(t, node),
                          let top = candidates.logits.first else { continue }
                    var blankRank: Int? = nil
                    var close = Float.infinity
                    for (rank, id) in candidates.ids.enumerated() {
                        let gap = top - candidates.logits[rank]
                        if id == decoder.blankId {
                            blankRank = rank
                            close = min(close, gap)
                            continue
                        }
                        if wordStartPieces.contains(id) { close = min(close, gap) }
                        guard let child = list.nodes[node].children[id] else { continue }
                        let reached = cost + gap
                        if reached <= list.maxDebt, reached < current[child] ?? .infinity {
                            current[child] = reached
                            back[key(t, child)] = (key(t, node), id, rank, gap)
                            if !pending.contains(child) {
                                pending.insert(child, at: pending.firstIndex { $0 > child }
                                    ?? pending.endIndex)
                            }
                        }
                    }
                    if node != 0, cost + close <= list.maxDebt {
                        for term in list.nodes[node].terms
                        where cost + close < best[term]?.debt ?? .infinity {
                            best[term] = (cost + close, key(t, node), close)
                        }
                    }
                    if t < endT, let rank = blankRank {
                        let gap = top - candidates.logits[rank]
                        let reached = cost + gap
                        if reached <= list.maxDebt, reached < next[node] ?? .infinity {
                            next[node] = reached
                            back[key(t + 1, node)] = (key(t, node), -1, rank, gap)
                        }
                    }
                }
                current = next
                if current.isEmpty || stats.stopped { break }
            }

            for (term, found) in best {
                var steps: [Detour.Step] = []
                var largest = found.close
                var cursor = found.key
                while let link = back[cursor] {
                    largest = max(largest, link.gap)
                    if link.piece >= 0 {
                        steps.append(Detour.Step(
                            frame: offset + startT + cursor / nodeCount,
                            piece: link.piece, rank: link.rank, gap: link.gap))
                    }
                    cursor = link.previous
                }
                steps.reverse()
                guard let endFrame = steps.last?.frame else { continue }
                // The normal pass's words this would replace: the word it
                // forked at, and each next word that began by the term's end.
                var lastWord = wordIndex
                while lastWord + 1 < wordStarts.count,
                      emissions[wordStarts[lastWord + 1]].frame <= endFrame {
                    lastWord += 1
                }
                let stop = lastWord + 1 < wordStarts.count ? wordStarts[lastWord + 1] : emissions.count
                let replaced = emissions[start..<stop]
                if replaced.map(\.token) == steps.map(\.piece) { continue }
                detours.append(Detour(
                    term: term, steps: steps, debt: found.debt, maxStepGap: largest,
                    heard: replaced.map { ($0.token, $0.frame) },
                    heardWordStarts: (wordIndex...lastWord).map { emissions[wordStarts[$0]].frame },
                    heardMinProbability: replaced.map(\.prob).min() ?? 0))
            }
        }
        return detours
    }

    /// Detours whose replaced tokens all survived the merge of overlapping
    /// windows, one per term and place, lowest debt first.
    static func kept(_ detours: [Detour], merged: [TokenWindow]) -> [Detour] {
        var present = Set<Int64>()
        for token in merged { present.insert(Int64(token.timestamp) << 20 | Int64(token.token)) }
        var seen = Set<String>()
        var kept: [Detour] = []
        for detour in detours.sorted(by: { $0.debt < $1.debt }) {
            guard detour.heard.allSatisfy({ present.contains(Int64($0.frame) << 20 | Int64($0.token)) })
            else { continue }
            let place = "\(detour.term)|\(detour.heardWordStarts.first ?? -1)|\(detour.steps.map(\.piece))"
            guard seen.insert(place).inserted else { continue }
            kept.append(detour)
            if kept.count == maximumDetours { break }
        }
        return kept.sorted { ($0.heardWordStarts.first ?? 0, $0.debt) < ($1.heardWordStarts.first ?? 0, $1.debt) }
    }

    /// Add the `bias` report to a protocol 2 response, when the pass ran.
    static func add(_ transcript: Transcript, recognizer: Recognizer, to response: inout [String: Any]) {
        guard let list = transcript.termList else { return }
        let pieces = recognizer.vocabulary.pieces
        let seconds = { (frame: Int) in WordEvidence.rounded(Double(frame) * secondsPerEncoderFrame) }
        response["bias"] = [
            "version": list.version,
            "terms": list.terms.count,
            "added_ms": WordEvidence.rounded(transcript.termPass.seconds * 1000),
            "forks": transcript.termPass.forks,
            "joint_calls": transcript.termPass.jointCalls,
            "decoder_calls": transcript.termPass.decoderCalls,
            "stopped": transcript.termPass.stopped,
            "detours": transcript.detours.map { detour -> [String: Any] in
                [
                    "term": list.terms[detour.term],
                    "written": recognizer.vocabulary.decode(ids: detour.steps.map(\.piece)),
                    "heard": recognizer.vocabulary.decode(ids: detour.heard.map(\.token)),
                    "heard_word_starts": detour.heardWordStarts.map(seconds),
                    "end": seconds(detour.steps.last?.frame ?? 0),
                    "debt": WordEvidence.rounded(Double(detour.debt)),
                    "max_step_gap": WordEvidence.rounded(Double(detour.maxStepGap)),
                    "heard_min_prob": WordEvidence.rounded(Double(detour.heardMinProbability)),
                    "steps": detour.steps.map { step -> [String: Any] in
                        [
                            "frame": step.frame,
                            "piece": pieces[step.piece] ?? "",
                            "rank": step.rank,
                            "gap": WordEvidence.rounded(Double(step.gap)),
                        ]
                    },
                ]
            },
        ]
    }
}
