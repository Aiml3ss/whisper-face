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

/// A decoded recording: its text and the merged token stream behind it.
struct Transcript {
    let text: String
    let tokens: [TokenWindow]
}

final class Recognizer {
    let layout = WindowLayout()
    let vocabulary: Vocabulary
    let decoder: TransducerDecoder
    private let encoder: MLModel
    private let mel: MelFeatures
    private let spliceSafeTokenIds: Set<Int>?
    private let caseVariantIds: [Int: Int]?

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
        let merger = WindowMerger()
        for chunkStart in layout.chunkStarts(totalSamples: samples.count) {
            let chunkEnd = min(chunkStart + layout.chunkSamples, samples.count)
            let windowTokens = try autoreleasepool {
                try transcribeWindow(samples: samples, chunkStart: chunkStart, chunkEnd: chunkEnd)
            }
            merged =
                merged.isEmpty
                ? windowTokens
                : merger.mergeChunks(
                    merged,
                    windowTokens,
                    spliceSafeTokenIds: spliceSafeTokenIds,
                    caseVariantIds: caseVariantIds
                )
        }
        merged.sort { $0.timestamp < $1.timestamp }
        merged = merger.collapseSeamWordDuplicates(merged, vocabulary: vocabulary.pieces)
        return Transcript(text: vocabulary.decode(ids: merged.map(\.token)), tokens: merged)
    }

    private func transcribeWindow(
        samples: [Float], chunkStart: Int, chunkEnd: Int
    ) throws -> [TokenWindow] {
        let validCount = chunkEnd - chunkStart

        var buffer = [Float](repeating: 0, count: layout.windowSamples)
        samples.withUnsafeBufferPointer { src in
            buffer.withUnsafeMutableBufferPointer { dst in
                dst.baseAddress!.update(from: src.baseAddress! + chunkStart, count: validCount)
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
        let emissions = try decoder.decode(
            encoded: encoded,
            frameRange: 0..<encoderLength,
            globalFrameOffset: chunkStart / layout.frameSamples
        )
        return emissions.map { (token: $0.token, timestamp: $0.frame, confidence: $0.prob, duration: 0) }
    }
}
