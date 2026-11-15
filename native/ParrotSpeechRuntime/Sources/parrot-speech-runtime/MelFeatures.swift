// Log-mel features for the Parakeet Unified encoder.
//
// Derived from FluidAudio (https://github.com/FluidInference/FluidAudio),
// tag v0.15.5, commit 19600a485baa4998812e4654b70d2bab8f2c9949:
// Sources/FluidAudio/Shared/AudioMelSpectrogram.swift and
// Sources/FluidAudio/ASR/Parakeet/Unified/UnifiedMelExtractor.swift.
// Copyright the FluidAudio authors. Licensed under the Apache License,
// Version 2.0 (native/ParrotSpeechRuntime/LICENSE-Apache-2.0.txt).
//
// Changed for Whisper Face: the two types are merged into one, only the
// centre-padded, frame-count-fixed path the offline encoder uses is kept,
// and the debug helpers, the unused array-of-arrays path and the generic
// buffer parameter are removed. Every arithmetic step that produces a
// feature value is kept in the same order with the same Accelerate calls,
// so the features, and therefore the transcripts, are the same.

import Accelerate
import CoreML
import Foundation

/// NeMo's `AudioToMelSpectrogramPreprocessor` as configured for
/// `nvidia/parakeet-unified-en-0.6b`: n_fft 512, a 400-sample symmetric Hann
/// window, hop 160, 128 Slaney mel bins, pre-emphasis 0.97, an additive log
/// guard of 2^-24, and per-feature normalisation over the valid frames.
final class MelFeatures {
    private let nMels: Int
    private let nFFT = 512
    private let hopLength = 160
    private let winLength = 400
    private let preemph: Float = 0.97
    private let logFloor: Float = powf(2, -24)
    private let padValue: Float = 0

    /// Samples in one zero-padded encoder window.
    let windowSamples: Int
    /// Mel frames the encoder expects for a window: `windowSamples / hop + 1`.
    let totalFrames: Int

    private let hannWindow: [Float]
    private let melFilterbankFlat: [Float]
    private var fftSetup: vDSP_DFT_Setup?

    // Reused between frames so the hot loop does not allocate.
    private var realIn: [Float]
    private var imagIn: [Float]
    private var realOut: [Float]
    private var imagOut: [Float]
    private var powerSpec: [Float]
    private var imagSq: [Float]
    private var frame: [Float]

    init(windowSamples: Int, nMels: Int = 128, sampleRate: Int = 16_000) {
        self.windowSamples = windowSamples
        self.nMels = nMels
        self.totalFrames = windowSamples / 160 + 1
        self.hannWindow = Self.createHannWindow(length: 400, periodic: false)
        let filterbank = Self.createMelFilterbank(
            nFFT: 512, nMels: nMels, sampleRate: sampleRate,
            fMin: 0.0, fMax: Float(sampleRate) / 2.0)
        let numFreqBins = 512 / 2 + 1
        var flat = [Float](repeating: 0, count: nMels * numFreqBins)
        for m in 0..<nMels {
            for f in 0..<numFreqBins {
                flat[m * numFreqBins + f] = filterbank[m][f]
            }
        }
        self.melFilterbankFlat = flat
        self.fftSetup = vDSP_DFT_zop_CreateSetup(nil, vDSP_Length(512), .FORWARD)
        self.realIn = [Float](repeating: 0, count: 512)
        self.imagIn = [Float](repeating: 0, count: 512)
        self.realOut = [Float](repeating: 0, count: 512)
        self.imagOut = [Float](repeating: 0, count: 512)
        self.powerSpec = [Float](repeating: 0, count: numFreqBins)
        self.imagSq = [Float](repeating: 0, count: numFreqBins)
        self.frame = [Float](repeating: 0, count: 512)
    }

    deinit {
        if let setup = fftSetup {
            vDSP_DFT_DestroySetup(setup)
        }
    }

    /// `(mel [1, nMels, totalFrames] float32, mel_length [1] int32)` for one
    /// encoder window whose first `validCount` samples are audio and the rest
    /// zero padding.
    func features(window: [Float], validCount: Int) throws -> (mel: MLMultiArray, length: MLMultiArray) {
        var flat = computeFlatTransposed(audio: window, expectedFrameCount: totalFrames)

        // NeMo `get_seq_len` with centre padding: floor(L / hop), one less
        // than the tensor's frame count, so the last frame is always zeroed.
        let validFrames = min(validCount / hopLength, totalFrames)
        normalizePerFeature(&flat, frames: totalFrames, validFrames: validFrames)

        let melArray = try MLMultiArray(
            shape: [1, NSNumber(value: nMels), NSNumber(value: totalFrames)], dataType: .float32)
        melArray.withUnsafeMutableBufferPointer(ofType: Float.self) { ptr, _ in
            // Contiguous [1, nMels, T]: element [0, m, t] is at m*T + t.
            for t in 0..<totalFrames {
                let base = t * nMels
                for m in 0..<nMels {
                    ptr[m * totalFrames + t] = flat[base + m]
                }
            }
        }

        let lengthArray = try MLMultiArray(shape: [1], dataType: .int32)
        lengthArray.withUnsafeMutableBufferPointer(ofType: Int32.self) { ptr, _ in
            ptr[0] = Int32(validFrames)
        }
        return (melArray, lengthArray)
    }

    /// NeMo `normalize_batch(..., "per_feature")`: per mel bin, subtract the
    /// mean and divide by the unbiased standard deviation (+1e-5) over the
    /// valid frames; padding frames become zero.
    private func normalizePerFeature(_ x: inout [Float], frames: Int, validFrames: Int) {
        guard validFrames > 0 else {
            for i in x.indices { x[i] = 0 }
            return
        }
        let denom = Float(validFrames > 1 ? validFrames - 1 : 1)
        for m in 0..<nMels {
            var mean: Float = 0
            for t in 0..<validFrames { mean += x[t * nMels + m] }
            mean /= Float(validFrames)

            var varSum: Float = 0
            for t in 0..<validFrames {
                let d = x[t * nMels + m] - mean
                varSum += d * d
            }
            let std = (varSum / denom).squareRoot() + 1e-5

            for t in 0..<frames {
                x[t * nMels + m] = t < validFrames ? (x[t * nMels + m] - mean) / std : 0
            }
        }
    }

    /// Raw log-mel, time-major `[frames * nMels]`, centre padded by n_fft/2
    /// on each side, exactly `expectedFrameCount` frames.
    private func computeFlatTransposed(audio: [Float], expectedFrameCount: Int) -> [Float] {
        let audioCount = audio.count
        let numFrames = expectedFrameCount

        guard numFrames > 0, let firstSample = audio.first else {
            return [Float](repeating: padValue, count: nMels)
        }

        let padLength = nFFT / 2
        let paddedCount = audioCount + 2 * padLength
        var paddedAudio = [Float](repeating: 0, count: paddedCount)

        // Pre-emphasis y[n] = x[n] - preemph * x[n-1], written straight into
        // the padded buffer. The sample before the window is taken as zero.
        let lastAudioSample: Float = 0
        paddedAudio[padLength] = firstSample - preemph * lastAudioSample
        if audioCount > 1 {
            paddedAudio.withUnsafeMutableBufferPointer { dstPtr in
                audio.withUnsafeBufferPointer { srcPtr in
                    let src = srcPtr.baseAddress!
                    let dst = dstPtr.baseAddress! + padLength + 1
                    var negPreemph = -preemph
                    vDSP_vsma(
                        src, 1,
                        &negPreemph,
                        src + 1, 1,
                        dst, 1,
                        vDSP_Length(audioCount - 1)
                    )
                }
            }
        }

        var mel = [Float](repeating: padValue, count: nMels * numFrames)
        let numFreqBins = nFFT / 2 + 1
        let windowOffset = (nFFT - winLength) / 2
        var melFrame = [Float](repeating: 0, count: nMels)

        for frameIdx in 0..<numFrames {
            let startIdx = frameIdx * hopLength
            vDSP_vclr(&frame, 1, vDSP_Length(nFFT))

            let audioStart = startIdx + windowOffset
            let availableSamples = min(winLength, paddedCount - audioStart)
            if availableSamples > 0 {
                paddedAudio.withUnsafeBufferPointer { paddedPtr in
                    hannWindow.withUnsafeBufferPointer { windowPtr in
                        frame.withUnsafeMutableBufferPointer { framePtr in
                            vDSP_vmul(
                                paddedPtr.baseAddress! + audioStart, 1,
                                windowPtr.baseAddress!, 1,
                                framePtr.baseAddress! + windowOffset, 1,
                                vDSP_Length(availableSamples)
                            )
                        }
                    }
                }
            }

            computePowerSpectrumInPlace()

            // melFrame = filterbank [nMels x bins] * powerSpec [bins]
            melFilterbankFlat.withUnsafeBufferPointer { filterPtr in
                powerSpec.withUnsafeBufferPointer { specPtr in
                    melFrame.withUnsafeMutableBufferPointer { outPtr in
                        vDSP_mmul(
                            filterPtr.baseAddress!, 1,
                            specPtr.baseAddress!, 1,
                            outPtr.baseAddress!, 1,
                            vDSP_Length(nMels),
                            vDSP_Length(1),
                            vDSP_Length(numFreqBins)
                        )
                    }
                }
            }

            for melIdx in 0..<nMels {
                mel[frameIdx * nMels + melIdx] = log(melFrame[melIdx] + logFloor)
            }
        }
        return mel
    }

    private func computePowerSpectrumInPlace() {
        guard let setup = fftSetup else { return }
        frame.withUnsafeBufferPointer { src in
            realIn.withUnsafeMutableBufferPointer { dst in
                _ = memcpy(dst.baseAddress!, src.baseAddress!, nFFT * MemoryLayout<Float>.size)
            }
        }
        vDSP_vclr(&imagIn, 1, vDSP_Length(nFFT))
        vDSP_DFT_Execute(setup, realIn, imagIn, &realOut, &imagOut)
        let numFreqBins = nFFT / 2 + 1
        vDSP_vsq(realOut, 1, &powerSpec, 1, vDSP_Length(numFreqBins))
        vDSP_vsq(imagOut, 1, &imagSq, 1, vDSP_Length(numFreqBins))
        vDSP_vadd(powerSpec, 1, imagSq, 1, &powerSpec, 1, vDSP_Length(numFreqBins))
    }

    private static func createHannWindow(length: Int, periodic: Bool) -> [Float] {
        var window = [Float](repeating: 0, count: length)
        let divisor = periodic ? Float(length) : Float(length - 1)
        for i in 0..<length {
            let phase = 2.0 * Float.pi * Float(i) / divisor
            window[i] = 0.5 * (1.0 - cos(phase))
        }
        return window
    }

    /// Slaney-normalised mel filterbank on the Slaney mel scale (librosa's
    /// default), `[nMels][nFFT/2 + 1]`.
    private static func createMelFilterbank(
        nFFT: Int, nMels: Int, sampleRate: Int, fMin: Float, fMax: Float
    ) -> [[Float]] {
        let numFreqBins = nFFT / 2 + 1

        func hzToMel(_ hz: Float) -> Float {
            let fSp: Float = 200.0 / 3.0
            let minLogHz: Float = 1000.0
            let minLogMel: Float = minLogHz / fSp
            let logStep: Float = log(6.4) / 27.0
            if hz >= minLogHz {
                return minLogMel + log(hz / minLogHz) / logStep
            } else {
                return hz / fSp
            }
        }

        func melToHz(_ mel: Float) -> Float {
            let fSp: Float = 200.0 / 3.0
            let minLogHz: Float = 1000.0
            let minLogMel: Float = minLogHz / fSp
            let logStep: Float = log(6.4) / 27.0
            if mel >= minLogMel {
                return minLogHz * exp(logStep * (mel - minLogMel))
            } else {
                return fSp * mel
            }
        }

        let melMin = hzToMel(fMin)
        let melMax = hzToMel(fMax)

        var melPoints = [Float](repeating: 0, count: nMels + 2)
        for i in 0..<(nMels + 2) {
            let mel = melMin + Float(i) * (melMax - melMin) / Float(nMels + 1)
            melPoints[i] = melToHz(mel)
        }

        var fftFreqs = [Float](repeating: 0, count: numFreqBins)
        for i in 0..<numFreqBins {
            fftFreqs[i] = Float(i) * Float(sampleRate) / Float(nFFT)
        }

        var filterbank = [[Float]](repeating: [Float](repeating: 0, count: numFreqBins), count: nMels)
        for melIdx in 0..<nMels {
            let fLeft = melPoints[melIdx]
            let fCenter = melPoints[melIdx + 1]
            let fRight = melPoints[melIdx + 2]
            let norm = 2.0 / (fRight - fLeft)
            for freqIdx in 0..<numFreqBins {
                let freq = fftFreqs[freqIdx]
                if freq >= fLeft && freq < fCenter {
                    filterbank[melIdx][freqIdx] = norm * (freq - fLeft) / (fCenter - fLeft)
                } else if freq >= fCenter && freq <= fRight {
                    filterbank[melIdx][freqIdx] = norm * (fRight - freq) / (fRight - fCenter)
                }
            }
        }
        return filterbank
    }
}
