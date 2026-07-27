# Selective Re-listen activation

Selective Re-listen is off by default. It can run only on macOS after this
machine produces an approved, content-free activation receipt from real local
recordings. Synthetic audio cannot authorize runtime behavior.

## Private manifest

`uv run scripts/capture_voice_evidence.py relisten` records this corpus
interactively and writes the manifest below; see
[docs/evidence/voice-corpora.md](evidence/voice-corpora.md). It never approves
anything. To build the corpus by hand instead:

Create a gitignored directory on the MacBook Pro containing mono 16 kHz PCM16
WAV microspans and a manifest:

```json
{
  "schema_version": 1,
  "kind": "whisper-face/relisten-activation-manifest",
  "cases": [
    {
      "case_id": "confirmed-01",
      "wav": "confirmed-01.wav",
      "expected_text": "invoice 2042",
      "expected_outcome": "confirmed",
      "evidence_type": "real-recorded"
    },
    {
      "case_id": "contradicted-01",
      "wav": "contradicted-01.wav",
      "expected_text": "invoice 2042",
      "expected_outcome": "contradicted",
      "evidence_type": "real-recorded"
    }
  ]
}
```

Use at least 40 real cases: at least 20 confirmed and 20 contradicted. Keep WAV,
expected text, and manifest private. The emitted report contains only aggregate
counts, decisions, refusals, and monotonic latency.

### Every take is capped at 2.4 seconds

The verifier only ever sees a microspan. `whisper_verifier_adapter` pins
`MAX_AUDIO_SAMPLES` to 2.4 s at 16 kHz, and
`benchmark_relisten_activation.read_microspan_wav` **rejects any longer WAV at
load**. A hand-recorded corpus of natural-length phrases therefore fails after
the recording time is already spent.

Record short, consequence-bearing spans — "invoice 2042", "transfer fifteen
hundred", "due March third" — not sentences. Expected text is separately capped
at 160 characters and 640 UTF-8 bytes.

`scripts/capture_voice_evidence.py relisten` hard-caps recording at the same
constant and stops the take when it is reached, which is the simplest way to
stay inside it.

Selective Re-listen needs no measurement mode: its benchmark drives the
verifier directly instead of asking the runtime to behave as if a receipt
existed, so nothing here is circular. That is the shape the other three gates
were made to match.

## Measure, review, approve

From the MacBook Pro checkout:

```sh
cd ~/dictation
uv run benchmark_relisten_activation.py \
  /private/path/relisten/manifest.json \
  --deadline-seconds 10 \
  --approve-runtime relisten_activation.json \
  --confirm-manual-review
```

Use `--confirm-manual-review` only after listening to every case and checking
its expected outcome. Approval fails unless the corpus contains no synthetic
cases and the prewarmed verifier reaches:

- at least 95% exact case decisions;
- p95 latency no greater than 650 ms;
- zero verifier refusals.

The resulting `relisten_activation.json` is mode `0600`, contains no transcript
or path, is gitignored, and is bound to the current pinned Whisper Tiny model
and policy thresholds.

Restart Whisper Face, open Last Recognition from the menu bar, then enable
Selective Re-listen. Removing or invalidating the receipt makes runtime fail
closed again. Turning the menu item off destroys the prewarmed verifier child.

After pulling a version that changes the verifier model, receipt schema, or
thresholds, rerun this workflow. Never copy an approval receipt between
machines as a substitute for device-specific evidence.
