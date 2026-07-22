# Neutral competitor evaluation

Last source review: **2026-07-21**

This page separates three kinds of evidence that are easy to blur together:

1. **Published claims** come from a product's official site or repository.
2. **Measured results** come from the same versioned task protocol on recorded
   hardware and software versions.
3. **Unavailable results** say plainly that a task was not run or could not be
   run. They are not scored as failures and are never replaced by a claim.

No cross-product physical run has been recorded yet, so this page does not name
a winner. The current snapshot is useful for selecting what to measure, not for
claiming Whisper Face is faster or more accurate.

## Published-position snapshot

| Product | Officially published position | Privacy/data position | Published price | Not measured here |
|---|---|---|---|---|
| **Whisper Face** | Mac-first, one-click local installation; Windows shares the core pipeline; no account or cloud dependency | ASR/cleanup are local, but private local history may contain raw/clean text and logs may contain transcript fragments. The experimental unauthenticated port-8787 endpoint listens beyond loopback and must remain on a trusted network ([privacy promise](../../PRIVACY.md)) | Free AGPL community edition; no usage cap | Comparative WER, protected-number accuracy, task latency, and physical app reliability |
| **Wispr Flow** | Native Mac, Windows, iPhone, and Android; custom dictionary/snippets, 100+ languages, and Pro command editing ([product](https://wisprflow.ai/), [pricing](https://wisprflow.ai/pricing)) | Its privacy page says transcription always occurs in the cloud; zero retention requires Privacy Mode on and Private Cloud Sync off ([privacy](https://wisprflow.ai/privacy), [data controls](https://wisprflow.ai/data-controls)) | Basic: free with desktop/iPhone word limits; Pro: $15 monthly or $12/user/month billed annually as reviewed | Fresh install, offline behavior, WER, number fidelity, latency, and insertion reliability |
| **Superwhisper** | Mac, Windows, and iOS; any-app dictation, local or cloud models, modes, vocabulary, and offline operation ([product](https://superwhisper.com/)) | Official materials describe fully local configurations and separately configurable cloud models; exact behavior therefore depends on configuration ([security guide](https://superwhisper.com/docs/security/sensitive-data), [privacy](https://superwhisper.com/privacy)) | Free tier; Pro advertised at $8.49/month, with annual and lifetime choices as reviewed | Exact free local-model entitlement, fresh install, WER, latency, and insertion reliability |
| **MacWhisper** | Mac-focused file/meeting transcription plus system dictation; local models, automation, and Pro workflows ([product and pricing](https://www.macwhisper.com/), [edition differences](https://docs.macwhisper.com/article/40-macwhisper-whisper-transcription-difference)) | Local transcription is available; optional cloud transcription/AI sends data to the selected provider ([privacy guide](https://docs.macwhisper.com/article/52-keeping-transcriptions-private)) | Free tier; Pro advertised at EUR 64 once with lifetime updates as reviewed | Current hardware footprint, fresh install, WER, dictation latency, and insertion reliability |
| **OpenWhispr** | MIT-licensed Mac/Windows/Linux Electron app with any-app dictation, local Whisper/Parakeet or optional cloud providers, meetings, notes, and agent features ([official repository](https://github.com/OpenWhispr/openwhispr)) | The project describes local-model transcription as on-device and cloud providers as optional; cloud and sync behavior depends on the selected mode | Free and open-source desktop application; optional hosted-service pricing was not reviewed | Packaged fresh install, mode-specific data flow, WER, number fidelity, latency, and insertion reliability |
| **Handy** | MIT-licensed Mac/Windows/Linux app with push-to-talk, local Whisper/Parakeet, VAD, and paste to the active field ([official repository](https://github.com/cjpais/Handy)) | The project describes core speech transcription as local; optional post-processing remains configuration-dependent | Free and open source | Fresh install, WER, number fidelity, post-processing data flow, latency, and insertion reliability |

Prices and features change. Recheck the linked sources before publishing a
marketing comparison.

## Reproducible task protocol

The source of truth is
[`benchmarks/competitor_tasks.json`](../../benchmarks/competitor_tasks.json).
Its six neutral tasks cover clean-machine setup, launch readiness, short
dictation, protected numbers and units, one correction, and a two-sentence
dictation. The corpus also fixes the latency start/stop boundary and the exact
interaction/error counting rules. Results retain per-task latency and
interaction values; they are never averaged across unlike tasks. Every product
run must record exactly one of:

- `measured`: completion, errors, latency, and interaction count with an
  artifact reference;
- `unavailable`: a closed reason and no numeric values; or
- `claimed_only`: an official source reference and no numeric values.

Evaluate one or more observation files offline:

```sh
uv run competitor_benchmark.py --protocol benchmarks/competitor_tasks.json \
  /path/to/whisper-face-run.json /path/to/other-product-run.json
```

The evaluator emits descriptive per-product aggregates in stable identifier
order. It deliberately emits no rank, winner, or inferred value.

## Current public synthetic scorecard

The repository also has deterministic internal evidence, which is not a
competitor benchmark:

```sh
uv run public_scorecard.py
```

It aggregates the checked-in Voice Compiler, consequence selector, insertion
simulation, Point-and-Speak, and Drop-to-Target corpora. The report is
transcript-free and explicitly reports zero physical apps, audio runs, or model
runs. Passing synthetic cases must not be presented as physical product
accuracy.
