# Acoustic accuracy activation

Acoustic calibration and pronunciation-keyword bias fail closed until the
MacBook Pro supplies its own physical evidence. Synthetic fixtures exercise
policy only and can never create runtime authority.

Run these commands from the installed MacBook Pro checkout:

```sh
cd ~/dictation
```

Do not copy audio, transcripts, manifests, or receipts into Git.

`uv run scripts/capture_voice_evidence.py calibration` and
`… keywords --keyword <term> --near-miss <word>` run these two corpora as guided
sessions and write the manifests below; see
[docs/evidence/voice-corpora.md](evidence/voice-corpora.md). Neither approves
anything. The manual paths remain below.

## Acoustic calibration

Create a private JSON manifest:

```json
{
  "schema_version": 1,
  "kind": "whisper-face/acoustic-calibration-activation-manifest",
  "telemetry": [],
  "cases": []
}
```

`telemetry` must contain at least eight valid `utterance_acoustic` objects from
the runtime performance trace. The policy accepts only its closed numeric
schema—never audio, text, device identity, or application context.

`cases` must contain at least 40 unique records, with at least eight each for
`clean`, `quiet`, `noisy`, and `long-pause`:

```json
{
  "case_token": "case-0000000000000001",
  "evidence_source": "physical-caller-attested",
  "condition": "quiet",
  "baseline": {
    "recognition_correct": false,
    "endpoint_correct": true
  },
  "candidate": {
    "recognition_correct": true,
    "endpoint_correct": true
  }
}
```

Record baseline and candidate outcomes from the same task set. Do not approve
unless the aggregate shows at least three recognition/end-point improvements,
zero regressions, and the case labels match the physical run.

```sh
uv run benchmark_acoustic_calibration_activation.py \
  /private/path/acoustic-calibration-manifest.json \
  --approve-runtime acoustic_calibration_activation.json \
  --confirm-manual-review
```

The resulting mode-`0600` receipt contains bounded settings and aggregate
counts only. Restart Whisper Face. A missing, malformed, stale-policy, or
insufficient receipt preserves the existing gain, silence, and endpoint
defaults. Reverb remains unavailable because current telemetry cannot measure
it.

## Pronunciation keyword

The candidate must first be eligible in
`acoustic_keyword_memory.json`: three exact correction observations and two
confirmations. Create a private manifest for exactly one candidate:

```json
{
  "schema_version": 1,
  "kind": "whisper-face/acoustic-keyword-activation-manifest",
  "keyword": "Qwen",
  "app_scope": null,
  "records": []
}
```

Supply at least 20 physical positive cases and 20 physical negative cases
using the closed record schema documented by
`acoustic_keyword_bias_evaluation.py`. All records must use
`physical-caller-attested`; synthetic and mixed batches cannot activate.
Approval requires at least three selection improvements and zero selection
regressions, positive candidate losses, or negative candidate introductions.

```sh
uv run benchmark_acoustic_keyword_activation.py \
  /private/path/keyword-manifest.json \
  --memory acoustic_keyword_memory.json \
  --approve-runtime acoustic_keyword_activation.json \
  --confirm-manual-review
```

The JSON report omits the keyword and case tokens. The private mode-`0600`
activation file binds aggregate evidence to the exact eligible candidate.
Restart Whisper Face; the term then receives bounded priority inside the local
Whisper prompt. It cannot rewrite recognized text. Forgetting the candidate
also removes its activation, and malformed or missing state has no effect.
