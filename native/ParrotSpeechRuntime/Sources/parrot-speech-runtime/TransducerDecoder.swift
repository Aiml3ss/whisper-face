// The greedy transducer (RNNT) decode loop for Parakeet Unified.
//
// Derived from FluidAudio (https://github.com/FluidInference/FluidAudio),
// tag v0.15.5, commit 19600a485baa4998812e4654b70d2bab8f2c9949:
// Sources/FluidAudio/ASR/Parakeet/Unified/UnifiedRnntDecoder.swift and
// Sources/FluidAudio/ASR/Parakeet/Unified/UnifiedFeatureProviders.swift.
// Copyright the FluidAudio authors. Licensed under the Apache License,
// Version 2.0 (native/ParrotSpeechRuntime/LICENSE-Apache-2.0.txt).
//
// Changed for Whisper Face: every window starts from a fresh state (the
// offline path reset it before every window anyway), input arrays are
// reused between steps instead of reallocated, and the joint model's top
// candidates are kept reachable at each step (`JointCandidates`,
// `stepObserver`, `GreedyTrace`) where the library discarded them, and the
// joint and prediction networks can be asked about a path the decode did
// not take (`jointCandidates`, `advance`), which the term pass in
// TermBias.swift uses. The token chosen at every step is still the joint
// model's own `token_id`: neither a step observer nor a trace can change
// it.

import CoreML
import Foundation

/// What the joint network offered at one decode step: its 64 best token
/// ids, best first, and their logits. The model computes these at every
/// step whether or not anyone reads them.
struct JointCandidates {
    let ids: [Int]
    let logits: [Float]
}

/// One call of the joint network.
struct DecodeStep {
    /// Global frame the step's window starts at. Frames inside the 2 s
    /// overlap of two windows are decoded once by each.
    let windowStartFrame: Int
    /// Global encoder frame (80 ms each) the step decoded.
    let frame: Int
    /// Which symbol of that frame this was: 0 for the first, up to
    /// `maxSymbolsPerFrame - 1`.
    let symbol: Int
    /// The token the decoder took. The blank id ends the frame.
    let chosen: Int
    /// The model's probability of `chosen`.
    let probability: Float
    let candidates: JointCandidates
}

/// What the normal pass saw, kept only for a request when the user has
/// terms (TermBias.swift): every joint step's candidates, and the decoder
/// state after each emitted token. Recording it reads arrays the model
/// already filled; it changes nothing the decode does.
final class GreedyTrace {
    struct Step {
        /// Window-local encoder frame.
        let t: Int
        /// How many tokens the window had emitted before this step: the
        /// decoder state the step ran from is `states[emitted]`.
        let emitted: Int
        let candidates: JointCandidates
    }

    var steps: [Step] = []
    /// `states[k]` is the decoder state after the window's first k tokens;
    /// `states[0]` is the window's starting state.
    var states: [TransducerDecoder.DecoderStep] = []
}

final class TransducerDecoder {
    struct Emission {
        /// Emitted (non-blank) token id.
        let token: Int
        /// Global encoder frame the token was emitted at.
        let frame: Int
        /// Softmax probability of the emitted token.
        let prob: Float
    }

    let blankId: Int
    let maxSymbolsPerFrame: Int
    private let decoderLayers: Int
    private let decoderHidden: Int
    private let decoderModel: MLModel
    private let jointModel: MLModel

    // Reused request arrays: the decoder's one-token input and its length.
    private let targets: MLMultiArray
    private let targetLength: MLMultiArray

    /// Called after every joint step, blank steps included, with what the
    /// joint network offered. It is read-only by construction: the decoder
    /// has already taken the model's own choice, and it continues with that
    /// choice whatever the observer does. Nothing in a release build sets
    /// it; the debug candidate dump does. (The term pass reads a
    /// `GreedyTrace` instead.)
    var stepObserver: ((DecodeStep) -> Void)?

    init(
        decoderModel: MLModel,
        jointModel: MLModel,
        blankId: Int = 1024,
        maxSymbolsPerFrame: Int = 10,
        decoderLayers: Int = 2,
        decoderHidden: Int = 640
    ) throws {
        self.decoderModel = decoderModel
        self.jointModel = jointModel
        self.blankId = blankId
        self.maxSymbolsPerFrame = maxSymbolsPerFrame
        self.decoderLayers = decoderLayers
        self.decoderHidden = decoderHidden
        self.targets = try MLMultiArray(shape: [1, 1], dataType: .int32)
        self.targetLength = try MLMultiArray(shape: [1], dataType: .int32)
        targetLength.withUnsafeMutableBufferPointer(ofType: Int32.self) { ptr, _ in
            ptr[0] = 1
        }
    }

    private func zeroState() throws -> MLMultiArray {
        let state = try MLMultiArray(
            shape: [NSNumber(value: decoderLayers), 1, NSNumber(value: decoderHidden)],
            dataType: .float32)
        state.withUnsafeMutableBufferPointer(ofType: Float.self) { ptr, _ in
            for index in ptr.indices { ptr[index] = 0 }
        }
        return state
    }

    /// Greedy RNNT over frames `frameRange` of `encoded` ([1, D, T]), from
    /// a fresh prediction-network state. Frames are reported as
    /// `globalFrameOffset + t`.
    func decode(
        encoded: MLMultiArray,
        frameRange: Range<Int>,
        globalFrameOffset: Int,
        trace: GreedyTrace? = nil
    ) throws -> [Emission] {
        var emissions: [Emission] = []
        var decoderStep = try runDecoder(
            token: Int32(blankId), h: try zeroState(), c: try zeroState())
        trace?.states.append(decoderStep)

        let dim = encoded.shape[1].intValue
        let encoderStep = try MLMultiArray(shape: [1, NSNumber(value: dim), 1], dataType: .float32)

        for t in frameRange {
            try copyEncoderStep(from: encoded, timeIndex: t, into: encoderStep, dim: dim)

            for symbol in 0..<maxSymbolsPerFrame {
                let jointOutput = try jointModel.prediction(
                    from: JointInput(encoderStep: encoderStep, decoderStep: decoderStep.output))
                guard let tokenArray = jointOutput.featureValue(for: "token_id")?.multiArrayValue,
                      tokenArray.count >= 1 else {
                    throw RuntimeError.decoding("joint model gave no token_id")
                }
                let tokenId = Self.firstInt32(tokenArray)

                if let observer = stepObserver {
                    observer(DecodeStep(
                        windowStartFrame: globalFrameOffset,
                        frame: globalFrameOffset + t,
                        symbol: symbol,
                        chosen: Int(tokenId),
                        probability: Self.probability(jointOutput),
                        candidates: Self.candidates(jointOutput)))
                }

                if let trace {
                    trace.steps.append(GreedyTrace.Step(
                        t: t, emitted: emissions.count,
                        candidates: Self.candidates(jointOutput)))
                }

                if tokenId == Int32(blankId) {
                    break
                }
                emissions.append(Emission(
                    token: Int(tokenId), frame: globalFrameOffset + t,
                    prob: Self.probability(jointOutput)))
                decoderStep = try runDecoder(token: tokenId, h: decoderStep.h, c: decoderStep.c)
                trace?.states.append(decoderStep)
            }
        }
        return emissions
    }

    /// The prediction network's output and LSTM state after a token.
    struct DecoderStep {
        let output: MLMultiArray
        let h: MLMultiArray
        let c: MLMultiArray
    }

    /// The state after taking `token` from `state`: what the decode would
    /// hold had it emitted `token` there. Used only by the term pass.
    func advance(_ token: Int, from state: DecoderStep) throws -> DecoderStep {
        try runDecoder(token: Int32(token), h: state.h, c: state.c)
    }

    /// The joint network's candidates at window-local frame `t` from
    /// `state`, read into `scratch` ([1, D, 1]). Used only by the term
    /// pass, for a path the decode did not take.
    func jointCandidates(
        encoded: MLMultiArray, t: Int, state: DecoderStep, scratch: MLMultiArray
    ) throws -> JointCandidates {
        try copyEncoderStep(from: encoded, timeIndex: t, into: scratch, dim: encoded.shape[1].intValue)
        let output = try jointModel.prediction(
            from: JointInput(encoderStep: scratch, decoderStep: state.output))
        return Self.candidates(output)
    }

    private func runDecoder(token: Int32, h: MLMultiArray, c: MLMultiArray) throws -> DecoderStep {
        targets.withUnsafeMutableBufferPointer(ofType: Int32.self) { ptr, _ in
            ptr[0] = token
        }
        let output = try decoderModel.prediction(
            from: DecoderInput(targets: targets, targetLength: targetLength, hIn: h, cIn: c))
        guard let decoderOut = output.featureValue(for: "decoder")?.multiArrayValue,
              let hOut = output.featureValue(for: "h_out")?.multiArrayValue,
              let cOut = output.featureValue(for: "c_out")?.multiArrayValue
        else {
            throw RuntimeError.decoding("decoder model gave no output")
        }
        // Decoder output is [1, 640, 1]; h/c are the state for the next
        // decoder call once this token is accepted.
        return DecoderStep(output: decoderOut, h: hOut, c: cOut)
    }

    private func copyEncoderStep(
        from encoded: MLMultiArray, timeIndex: Int, into step: MLMultiArray, dim: Int
    ) throws {
        guard encoded.dataType == .float32 else {
            throw RuntimeError.decoding("encoder output is not float32")
        }
        let srcPtr = encoded.dataPointer.bindMemory(to: Float.self, capacity: encoded.count)
        let dstPtr = step.dataPointer.bindMemory(to: Float.self, capacity: step.count)
        let stride1 = encoded.strides[1].intValue
        let stride2 = encoded.strides[2].intValue
        for c in 0..<dim {
            dstPtr[c] = srcPtr[c * stride1 + timeIndex * stride2]
        }
    }

    private static func firstInt32(_ array: MLMultiArray) -> Int32 {
        array.withUnsafeBufferPointer(ofType: Int32.self) { $0[0] }
    }

    private static func probability(_ output: MLFeatureProvider) -> Float {
        guard let array = output.featureValue(for: "token_prob")?.multiArrayValue,
              array.count >= 1, array.dataType == .float32 else { return 0 }
        return array.withUnsafeBufferPointer(ofType: Float.self) { $0[0] }
    }

    /// The joint network's `top_k_ids` and `top_k_logits`, or nothing if a
    /// model without them is loaded.
    static func candidates(_ output: MLFeatureProvider) -> JointCandidates {
        guard let ids = output.featureValue(for: "top_k_ids")?.multiArrayValue,
              let logits = output.featureValue(for: "top_k_logits")?.multiArrayValue,
              ids.dataType == .int32, logits.dataType == .float32,
              ids.count == logits.count else {
            return JointCandidates(ids: [], logits: [])
        }
        let count = ids.count
        let idValues = ids.withUnsafeBufferPointer(ofType: Int32.self) { pointer in
            (0..<count).map { Int(pointer[$0]) }
        }
        let logitValues = logits.withUnsafeBufferPointer(ofType: Float.self) { pointer in
            (0..<count).map { pointer[$0] }
        }
        return JointCandidates(ids: idValues, logits: logitValues)
    }
}

// MARK: - Model inputs

/// Encoder input: `mel` [1, 128, 1501] float32 and `mel_length` [1] int32.
final class EncoderInput: MLFeatureProvider {
    let featureNames: Set<String> = ["mel", "mel_length"]
    private let mel: MLFeatureValue
    private let melLength: MLFeatureValue

    init(mel: MLMultiArray, melLength: MLMultiArray) {
        self.mel = MLFeatureValue(multiArray: mel)
        self.melLength = MLFeatureValue(multiArray: melLength)
    }

    func featureValue(for featureName: String) -> MLFeatureValue? {
        switch featureName {
        case "mel": return mel
        case "mel_length": return melLength
        default: return nil
        }
    }
}

/// Prediction-network input: one target token and the LSTM state.
final class DecoderInput: MLFeatureProvider {
    let featureNames: Set<String> = ["targets", "target_length", "h_in", "c_in"]
    private let targets: MLFeatureValue
    private let targetLength: MLFeatureValue
    private let hIn: MLFeatureValue
    private let cIn: MLFeatureValue

    init(targets: MLMultiArray, targetLength: MLMultiArray, hIn: MLMultiArray, cIn: MLMultiArray) {
        self.targets = MLFeatureValue(multiArray: targets)
        self.targetLength = MLFeatureValue(multiArray: targetLength)
        self.hIn = MLFeatureValue(multiArray: hIn)
        self.cIn = MLFeatureValue(multiArray: cIn)
    }

    func featureValue(for featureName: String) -> MLFeatureValue? {
        switch featureName {
        case "targets": return targets
        case "target_length": return targetLength
        case "h_in": return hIn
        case "c_in": return cIn
        default: return nil
        }
    }
}

/// Joint input: one encoder frame and the prediction network's output.
final class JointInput: MLFeatureProvider {
    let featureNames: Set<String> = ["encoder_step", "decoder_step"]
    private let encoderStep: MLFeatureValue
    private let decoderStep: MLFeatureValue

    init(encoderStep: MLMultiArray, decoderStep: MLMultiArray) {
        self.encoderStep = MLFeatureValue(multiArray: encoderStep)
        self.decoderStep = MLFeatureValue(multiArray: decoderStep)
    }

    func featureValue(for featureName: String) -> MLFeatureValue? {
        switch featureName {
        case "encoder_step": return encoderStep
        case "decoder_step": return decoderStep
        default: return nil
        }
    }
}
