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

## Measurement mode: how the candidate arm is measured at all

Both gates want an A/B. Both candidate arms — calibrated front end, biased
keyword — are behavior the runtime only produced from the receipt the A/B is
supposed to authorize, so neither arm could be recorded. That was a circular
gate, not a hard corpus.

Start the runtime for the candidate pass with an explicit `--measure` argument:

```sh
launchctl bootout gui/$UID/com.berg.dictate      # stop the installed service
uv run --locked --script dictate.py \
  --measure calibration:gain=2.5,noise=0.008,vad=0.012,end-silence=280
uv run --locked --script dictate.py --measure keyword:Qwen
```

Stop the LaunchAgent first. Whisper Face holds a single-instance lock, so a
second copy started with `--measure` exits immediately and silently while the
ordinary one keeps running — you would record a whole pass against unmodified
behavior without noticing. Bring the service back with
`launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.berg.dictate.plist`
afterwards. The startup banner and the menu-bar row are how you confirm the
copy you are talking to is the measured one.

What it is: the real code path. Calibrated gain, noise gate, VAD, and
end-silence go through the same `prepare_asr_audio`, `calibrated_vad_threshold`,
and `calibrated_end_silence_seconds` an approved receipt drives, bounded by the
same policy limits; the measured keyword enters the same Whisper prompt an
approved activation would put it in.

What it is not:

- **Not a receipt.** Nothing is written. `measurement_mode.py` imports no
  activation module and opens no file, so there is no code path from it to an
  activation receipt.
- **Not persistent.** It comes from process arguments only, never from
  `preferences.json`, and ends when you quit the runtime.
- **Not invisible.** The runtime prints a `[measurement]` banner at startup, the
  menu bar carries a "Measurement mode: … — evidence only" row while it is on,
  and `runtime_status_snapshot()` reports the arms.
- **Not exempt.** Evidence recorded under it still faces every threshold and
  still needs `--confirm-manual-review`.

Any malformed or out-of-policy argument turns **every** arm off and prints why,
so a half-configured session cannot silently measure something else.

Manifests recorded this way carry `"measurement_mode"` naming the arm, the
benchmarks read it, and it lands in the receipt. A measured corpus is acceptable
— it measures the shipping path, which is the point — and stays visibly
distinct from ordinary-path evidence.

## Acoustic calibration

Create a private JSON manifest:

```json
{
  "schema_version": 1,
  "kind": "whisper-face/acoustic-calibration-activation-manifest",
  "measurement_mode": "measured-calibration-candidate",
  "telemetry": [],
  "cases": []
}
```

`telemetry` must contain at least eight valid `utterance_acoustic` objects from
the runtime performance trace. The policy accepts only its closed numeric
schema—never audio, text, device identity, or application context.

`measurement_mode` is optional and says how the **candidate** pass was
produced: omit it (or write `"ordinary-path"`) when the runtime already had an
approved receipt, and write `"measured-calibration-candidate"` when the
candidate pass ran under `--measure calibration:…`. Omitting it is a claim that
the candidate arm ran on the ordinary path, so do not omit it after a measured
session. No other value is accepted.

`cases` must contain at least 40 unique records, with at least eight each for
`clean`, `quiet`, `noisy`, and `long-pause`:

```json
{
  "case_token": "case-8f3b21c9a04e7d16",
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

Case tokens are `case-` plus 16 random hex characters. Generate them the way
the capture tool does — `secrets.token_hex(8)` — not as a sequence: a
sequential token is valid but trivially correlatable across manifests.

Record baseline and candidate outcomes from the same task set, with the
candidate pass under measurement mode. Do not approve unless the aggregate
shows at least three recognition/end-point improvements, zero regressions, and
the case labels match the physical run.

Nothing links the `telemetry` block to the `cases`: the telemetry decides the
settings, the cases are categorical A/B labels, and no check proves they came
from the same machine, session, or era. Import the telemetry from the log of
the same machine and the same period you record the corpus on, and re-import it
if you re-record.

```sh
uv run benchmark_acoustic_calibration_activation.py \
  /private/path/acoustic-calibration-manifest.json \
  --approve-runtime acoustic_calibration_activation.json \
  --confirm-manual-review
```

The resulting mode-`0600` receipt contains bounded settings, aggregate counts,
and the `measurement_mode` label only. Restart Whisper Face **without**
`--measure` so the receipt, not the override, is what applies. A missing,
malformed, stale-policy, or insufficient receipt preserves the existing gain,
silence, and endpoint defaults. Reverb remains unavailable because current
telemetry cannot measure it.

## Pronunciation keyword

The candidate must first be eligible in
`acoustic_keyword_memory.json`: three exact correction observations and two
confirmations. **Earn that before booking the session.** Eligibility comes only
from real dictation. Dictate normally and, when the term is misrecognized,
correct it in place within the correction window; the runtime's exact-range
correction validator feeds `remember_explicit_acoustic_keyword_correction`,
which counts each accepted correction once in the observation channel and once
in the confirmation channel. **Three exact corrections is therefore enough.**

Nothing here can manufacture that, measurement mode does not touch keyword
memory, and the benchmark refuses an ineligible candidate — so check first:

```sh
uv run scripts/capture_voice_evidence.py keywords \
  --keyword Qwen --near-miss Gwen --plan
```

which prints the candidate's observation and confirmation counts read-only.

Create a private manifest for exactly one candidate:

```json
{
  "schema_version": 1,
  "kind": "whisper-face/acoustic-keyword-activation-manifest",
  "measurement_mode": "measured-keyword-priority",
  "keyword": "Qwen",
  "app_scope": null,
  "records": []
}
```

`measurement_mode` is optional and says how the **biased** pass was produced:
omit it (or write `"ordinary-path"`) if the term was already activated, and
write `"measured-keyword-priority"` when the biased pass ran under
`--measure keyword:<term>`. No other value is accepted.

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
activation file binds aggregate evidence to the exact eligible candidate and
records its `measurement_mode` label. Restart Whisper Face **without**
`--measure`; the term then receives bounded priority inside the local Whisper
prompt from the activation rather than the override. It cannot rewrite
recognized text. Forgetting the candidate also removes its activation, and
malformed or missing state has no effect.
