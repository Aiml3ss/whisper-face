# FluidAudio upstream ask: a tagged release containing PR #814

Status, 2026-07-27: the API this repository needs — token timings and
per-token confidence from `UnifiedAsrManager`'s offline batch path — already
landed upstream in FluidAudio PR #814 (merged 2026-07-22, commit
`f8529217d468a643c0b77bcf7e382569675b9559`). It is not in any tagged release;
v0.15.5 is the newest tag and our `native/ParrotASRHelper/Package.swift` pins
it exactly. The remaining ask is a release, not a feature.

Before filing, check whether a tag newer than v0.15.5 exists
(`git ls-remote --tags https://github.com/FluidInference/FluidAudio`). If one
containing `transcribeWithTimings` exists, do not file — update the pin
instead (upgrade notes: `native/ParrotASRHelper/PROTOCOL.md`, "The upgrade
that enables v2").

File with: `gh issue create -R FluidInference/FluidAudio --title ... --body ...`

---

Title:

    Release request: tag a version that includes #814 (transcribeWithTimings)

Body:

    We ship Whisper Face, an AGPL macOS dictation app. Its native ASR helper
    pins FluidAudio to exact release tags (currently 0.15.5) and uses
    `UnifiedAsrManager` with the offline 15 s encoder.

    #814 added `transcribeWithTimings(_:)` to the unified offline batch path.
    That is exactly what we need to surface word timings and token confidence
    to our runtime, and we have verified our helper against commit f8529217:
    it builds unchanged, and the new API returns correct timings and
    confidences for our test audio alongside an identical transcript.

    Could you tag a release that includes #814? We prefer pinning tags over
    commits for reproducible builds. If a release is already planned, the
    expected timing would be enough for us to decide whether to pin the
    commit in the meantime.

    One observation from building at f8529217, in case it is unintentional:
    ASR-only consumers now also download the NemoTextProcessing.xcframework
    binary artifact (~47 MB, statically linked) that came in with the TTS
    text-normalization work. Not a blocker for us.

---

Not part of this ask: `buildWordTimings(from:)` drops `TokenTiming.confidence`
when grouping tokens into `WordTiming`. We group tokens ourselves in the
helper and keep per-word confidence, so no upstream change is needed for
that.
