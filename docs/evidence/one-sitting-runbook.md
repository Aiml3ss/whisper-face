# One-sitting runbook: earning the gated features

Four features ship off and turn on only from a receipt this Mac earns from its
own physical evidence, reviewed by you. This page is the ordering layer: what
to type and what to say, in sequence, with time budgets. The full protocols
live in [physical-sessions.md](physical-sessions.md) and
[voice-corpora.md](voice-corpora.md) — read the relevant section there before
each session; this page will not repeat the reasoning.

Honest total: about 9–10 hours of operator time. That is two sittings of
roughly five hours, not one afternoon. Every tool resumes mid-session, so any
split is safe.

| Feature | Receipt it needs (repo root, `0600`, gitignored) |
|---|---|
| Selective Re-listen | `relisten_activation.json` |
| Acoustic calibration | `acoustic_calibration_activation.json` |
| Acoustic keyword priority | `acoustic_keyword_activation.json` |
| Delayed cleanup | `delayed_cleanup_activation.json` |

The fifth "Built, gated" item in [capabilities.md](../capabilities.md), the
Acoustic Time Machine, needs no evidence session: it is an ordinary opt-in
preference (`acoustic_time_machine`, default off) with no receipt.

---

## Prerequisites — 15 minutes, once

Run everything from the checkout root (the directory holding `dictate.py`).

1. **Installation verifies.**

   ```sh
   ./setup.sh --verify
   ```

   This checks the launcher, the locked runtime command, the LaunchAgent, the
   pinned models, and the native helper. Fix anything red before booking time.

2. **The service is running and has dictated.** Hold Right Option, speak a
   sentence into TextEdit, release. Text appearing proves microphone and
   Accessibility grants in one step. Then confirm the log exists:

   ```sh
   ls transcripts.jsonl dictate.log
   ```

   Both live next to `dictate.py`. The LaunchAgent writes the runtime's
   stdout to `dictate.log`; several sessions below read it.

3. **The recorder can hear.** The voice-corpus tool records at 16 kHz mono and
   refuses without a working input device:

   ```sh
   uv run scripts/capture_voice_evidence.py relisten --list-devices
   ```

4. **Keyword eligibility is seeded — days ahead, not today.** The keyword
   session refuses any term without three observations and two confirmations
   in `acoustic_keyword_memory.json`, earned only during real dictation. In
   practice: make **three exact in-place corrections** of the term during
   normal use, spread over normal days. Check before booking two hours:

   ```sh
   uv run scripts/capture_voice_evidence.py keywords \
     --keyword Qwen --near-miss Gwen --plan
   ```

   The eligibility line it prints is read-only; nothing here can manufacture
   it. If the term is not eligible yet, do everything else and come back.

5. **Know the two service commands.** Measurement sessions need the installed
   service stopped (single-instance lock) and restarted afterwards:

   ```sh
   launchctl bootout gui/$UID/com.berg.dictate
   launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.berg.dictate.plist
   ```

---

## The plan

| Order | Session | Time | Produces |
|---|---|---|---|
| A | 20+ warm dictations | 30 min | warm-path aggregates + the telemetry session C imports |
| B | Selective Re-listen corpus | 1 h | `relisten_activation.json` |
| C | Acoustic calibration A/B | 2.5 h | `acoustic_calibration_activation.json` |
| D | Delayed-cleanup 50-case grid | 3.5–4 h | `delayed_cleanup_activation.json` |
| E | Keyword bias A/B | 2–2.5 h | `acoustic_keyword_activation.json` |

Suggested split: sitting one is A, B, and D (the grid is the heavy one — do it
fresh); sitting two is C and E. A must precede C, because C eats A's
telemetry. E must come after its eligibility exists (prerequisite 4).

Dictate only the neutral phrases the tools print. Never dictate real work into
an evidence session.

---

## Session A — twenty warm dictations (30 minutes)

No flag and no special mode. The runtime always prints closed-schema `[trace]`
JSON lines, and the LaunchAgent already routes them into `dictate.log` at the
checkout root.

**Do:** with the ordinary service running, dictate at least 20 utterances of
normal length into a scratch TextEdit document. Vary length; a few long ones
help.

**Then:**

```sh
uv run performance_lab.py warm-path --trace-log dictate.log
```

**Success looks like:** a per-stage p50/p95 table with `records:` at 20 or
more. No receipt appears — this session unlocks no gate. It exists because
(1) it is the baseline evidence the hardware-matrix checklist wants, and
(2) the same log now holds the `utterance_acoustic` telemetry records session
C requires (at least 8 records, at least 8 seconds of speech total — 20
dictations clears both). There is no review step; the aggregate is
transcript-free by construction.

---

## Session B — Selective Re-listen (1 hour)

Detail: [voice-corpora.md, "Selective Re-listen"](voice-corpora.md). No
measurement mode; the benchmark drives the verifier directly, so the running
service is irrelevant.

**Record — about 30 minutes.**

```sh
uv run scripts/capture_voice_evidence.py relisten
```

40 cases, interleaved: 20 **confirmed** (say the phrase the manifest expects)
and 20 **contradicted** (say the deliberately different value while the
manifest keeps the original). The tool shows both lines, labelled
`SAY THIS OUT LOUD` and `MANIFEST EXPECTS`. Takes are capped at 2.4 seconds;
keys are `[r]` record, `[p]` play, `[a]` accept, `[s]` skip, `[q]` quit and
resume. Everything lands in `.evidence/relisten/` (`0700`/`0600`).

**Review — the gate. Listen to all 40:**

```sh
uv run scripts/capture_voice_evidence.py relisten --review
```

**Evaluate, then approve by hand:**

```sh
uv run benchmark_relisten_activation.py .evidence/relisten/manifest.json \
  --deadline-seconds 10
uv run benchmark_relisten_activation.py .evidence/relisten/manifest.json \
  --deadline-seconds 10 \
  --approve-runtime relisten_activation.json \
  --confirm-manual-review
```

`--confirm-manual-review` is your attestation that you listened; nothing sets
it for you. The gate needs, from `relisten_activation.py`: 40+ real samples,
20+ per outcome, zero synthetic cases, exact accuracy ≥ 95.0% on the
`prewarmed_whisper_tiny` engine, p95 latency ≤ 650 ms, zero refusals.

**Success looks like:** `relisten_activation.json` at the repo root.

---

## Session C — acoustic calibration A/B (2.5 hours)

Detail: [voice-corpora.md, "Acoustic calibration"](voice-corpora.md). Two
passes over the same 40 tasks: 10 each of `clean`, `quiet`, `noisy`, and
`long-pause` (the gate floor is 8 per condition, 40 total, from
`acoustic_calibration_activation.py`).

**Import telemetry and see the plan:**

```sh
uv run scripts/capture_voice_evidence.py calibration \
  --telemetry-log dictate.log --plan
```

This prints the candidate settings the policy derives from session A's
telemetry, and the exact `--measure` command for the candidate pass. If it
says there is no candidate yet, dictate more and re-import.

**Baseline pass — service running normally, no `--measure`:**

```sh
uv run scripts/capture_voice_evidence.py calibration --arm baseline
```

Per case: set up the printed condition (fan on, sit back, pause mid-sentence —
the tool describes each), dictate the printed sentence into Whisper Face for
real, record the witness take, then answer the two questions about the run you
just saw: did it recognize the words correctly, and did it end the utterance
at the right moment.

**Candidate pass — measured runtime.** Stop the service, start the runtime
with the exact command the tool printed (values come from your telemetry; the
bounds are the policy's own — gain 1.0–4.0, noise gate 0.004–0.03, VAD
0.006–0.05, end-silence 180–600 ms, noise below VAD):

```sh
launchctl bootout gui/$UID/com.berg.dictate
uv run --locked --script dictate.py \
  --measure calibration:gain=2.5,noise=0.008,vad=0.012,end-silence=280
```

Confirm the `[measurement]` banner and menu-bar row, then:

```sh
uv run scripts/capture_voice_evidence.py calibration --arm candidate
```

The tool asks once whether measurement mode is on and writes the answer into
the manifest. Use `--witness first` if recording both passes takes too long.

**Review, evaluate, approve:**

```sh
uv run scripts/capture_voice_evidence.py calibration --review
uv run benchmark_acoustic_calibration_activation.py \
  .evidence/calibration/manifest.json
uv run benchmark_acoustic_calibration_activation.py \
  .evidence/calibration/manifest.json \
  --approve-runtime acoustic_calibration_activation.json \
  --confirm-manual-review
```

The gate needs: 40+ cases, 8+ per condition, **zero** recognition or endpoint
regressions, and at least 3 improvements across the two questions combined.
The candidate can honestly fail to beat the baseline; then there is no
receipt, and that is the gate working.

**Afterwards:** quit the measured runtime and
`launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.berg.dictate.plist`.

---

## Session D — delayed-cleanup 50-case grid (3.5–4 hours)

Detail: [physical-sessions.md, Session 2](physical-sessions.md). The grid is
four surfaces by four scenarios, from `delayed_cleanup_activation.py`:

| Axis | Values | Gate floor |
|---|---|---|
| surface | `native-text`, `web-text`, `electron-editor`, `terminal-editor` | 10 each |
| scenario | `unchanged`, `edit-elsewhere`, `edit-overlap`, `focus-drift` | 8 each |

The 50-case plan lands 13/13/12/12 on both axes. It also needs at least 15
`applied` and 15 not-applied outcomes; the plan predicts 25/25
(`unchanged` and `edit-elsewhere` apply; `edit-overlap` yields
`no_safe_changes`; `focus-drift` yields `focus_drift`). Never adjust a
prediction after seeing a result.

**Start the measured runtime, with its output going somewhere the capture
tool can read.** The capture tool parses the runtime's own
`[delayed-cleanup]` line from `dictate.log` — but a runtime started in a
terminal prints to that terminal, not to the log (only the LaunchAgent
redirects). So append it yourself, unbuffered:

```sh
launchctl bootout gui/$UID/com.berg.dictate
PYTHONUNBUFFERED=1 uv run --locked --script dictate.py \
  --measure delayed-cleanup 2>&1 | tee -a dictate.log
```

Without the redirect every case blocks on `no-runtime-line`. Confirm the
`[measurement]` banner in the tee'd output.

**Run the grid in a second terminal:**

```sh
uv run scripts/capture_delayed_cleanup_cases.py plan
uv run scripts/capture_delayed_cleanup_cases.py run
```

Per case, about 3.5 minutes: open the printed surface (the tool suggests
TextEdit/Notes for `native-text`, a browser text area, Obsidian/Slack/VS Code
for `electron-editor`, a terminal editor in insert mode), dictate the
disfluent phrase the tool prints (a clean phrase gives the delayed pass
nothing to propose), perform the scenario action — nothing, type a word away
from the span, edit inside the span, or click into another window — wait for
the delayed pass, then answer the four safety questions (wrong-target write,
overwritten edit, moved selection, duplicate write). `apply_ms` comes only
from the runtime's own line; the tool never lets you stopwatch it. Work in
blocks of one surface at a time, and split across sittings freely — the
session resumes.

**Emit, review all 50 yourself, then install the receipt:**

```sh
uv run scripts/capture_delayed_cleanup_cases.py summary
uv run scripts/capture_delayed_cleanup_cases.py emit \
  --out delayed_cleanup_physical_cases.json
uv run delayed_cleanup_activation.py delayed_cleanup_physical_cases.json \
  --manual-reviewed --write-receipt delayed_cleanup_activation.json
```

`--manual-reviewed` is your personal attestation; the capture tool never
passes it. The gate additionally requires zero expected/actual mismatches,
zero safety failures, and p95 apply latency ≤ 150 ms.

**Afterwards:** quit the measured runtime, restore the service.

---

## Session E — keyword bias A/B (2–2.5 hours)

Detail: [voice-corpora.md, "Pronunciation keyword"](voice-corpora.md). One
keyword per session. 40 records: 20 **positive** sentences containing the hard
name, 20 **negative** sentences containing a near miss that is not it. The
floors come from `acoustic_keyword_bias_evaluation.py`: at least 20 positive,
at least 20 negative, at least 3 selection improvements, zero regressions.

**Confirm eligibility first** (prerequisite 4), then run the **unbiased
pass** — keyword absent from the prompt: remove the term from
`dictionary.txt` if present, run the ordinary service with no `--measure`:

```sh
uv run scripts/capture_voice_evidence.py keywords \
  --keyword Qwen --near-miss Gwen --arm unbiased
```

Per case per pass, dictate the printed sentence into Whisper Face and answer:
did the keyword appear anywhere in the recognized candidates, and was it the
term actually selected into the text. "Selected but not a candidate" is
rejected as impossible.

**Biased pass — measured runtime.** Do not use `dictionary.txt` for this; it
reaches the prompt by a different route. Measurement mode puts the term
exactly where an approved activation would:

```sh
launchctl bootout gui/$UID/com.berg.dictate
uv run --locked --script dictate.py --measure keyword:Qwen
```

then:

```sh
uv run scripts/capture_voice_evidence.py keywords \
  --keyword Qwen --near-miss Gwen --arm biased
```

The negatives are the safety half: a biased prompt that starts hearing the
keyword in sentences that never contained it is a regression, and the gate
will refuse. Correctly.

**Review, evaluate, approve:**

```sh
uv run scripts/capture_voice_evidence.py keywords --review
uv run benchmark_acoustic_keyword_activation.py \
  .evidence/keywords/manifest.json \
  --memory acoustic_keyword_memory.json
uv run benchmark_acoustic_keyword_activation.py \
  .evidence/keywords/manifest.json \
  --memory acoustic_keyword_memory.json \
  --approve-runtime acoustic_keyword_activation.json \
  --confirm-manual-review
```

**Success looks like:** an entry for the term in
`acoustic_keyword_activation.json`. Restore the service without `--measure`.

---

## After every receipt

Restart Whisper Face **without** `--measure`, so what runs afterwards is the
approved receipt rather than the session override. A receipt is bound to this
Mac, this microphone, and the pinned model and thresholds; re-record after any
of those change, and never copy audio, manifests, or receipts anywhere. See
["These receipts do not travel"](voice-corpora.md).

## What this rig cannot do for you

- **No case can be produced without you at the microphone.** Every tool above
  records what a human actually spoke and observed; none generates audio,
  infers a label, or defaults an answer. Synthetic evidence is structurally
  refused: the re-listen gate rejects any manifest with a synthetic case, the
  keyword gate requires `synthetic_cases: 0`, and the delayed-cleanup gate
  accepts only `caller-attested-physical` records.
- **No tool approves anything.** The capture harnesses stop and print the
  command; `--confirm-manual-review` and `--manual-reviewed` are yours to
  pass, after you have listened to or re-read every case.
- **Measurement mode is not a shortcut.** It applies the real candidate code
  path for one process session, writes nothing, labels every artifact it
  touched, and the label is carried into the receipt.
- **A gate may honestly refuse.** Calibration and keyword receipts require
  the candidate to actually help (three or more improvements, zero
  regressions). If it does not, the feature stays off, and recording the
  session again will not change that unless the settings or the term do.

## Blockers and gaps found while cross-checking this page

- **Foreground measured runtime does not reach `dictate.log` on its own.**
  `dictate.py` prints `[delayed-cleanup]` lines to stdout; only the
  LaunchAgent redirects stdout into `dictate.log`, and measurement sessions
  require the LaunchAgent stopped. Session D above bridges this with
  `PYTHONUNBUFFERED=1 … | tee -a dictate.log`; neither
  [physical-sessions.md](physical-sessions.md) nor the capture tool's own
  docstring mentions the redirect, and without it all 50 cases block on
  `no-runtime-line`.
- **Two hardware-checklist commands in physical-sessions.md do not match the
  code.** `performance_lab.py startup` requires `--cold-trace-log` and
  `--warm-trace-log` (there is no `startup --trace-log`), and
  `performance_lab.py evaluate` requires `--observations`. Both fail as
  written in Checklist B. They affect ledger row 11, not any gate.
- **Keyword eligibility is a calendar dependency.** Three in-place corrections
  during real dictation must already exist in `acoustic_keyword_memory.json`;
  no command in this runbook can create them on the day.
