# Passive evidence observation

You already use Whisper Face. Every time you dictate, the runtime writes down
what its insertion receipt proved — into which app, with what outcome, with
what capability buckets. That is real physical evidence about real apps, and
until now it was thrown away because it did not arrive inside a scripted
session.

Two tools can now harvest it:

```sh
uv run scripts/capture_app_matrix.py observe
uv run scripts/capture_lifecycle_evidence.py observe
```

Both are read-only against your dictation history, ask you nothing, write the
same artifacts the guided sessions write, and take a second to run. Run them
whenever you are curious. Re-running is idempotent.

This page is mostly about what passive observation **cannot** do, because that
is the part that is easy to get wrong.

## The one thing to understand

The guided session asks you a question a machine cannot answer: *did the
correct text appear in the intended target?* Passive use has nobody to ask.

It does not follow that passive evidence is weaker. For a **readable**
destination, a `verified` receipt is mechanically proven delivery — the
runtime read the field back and confirmed every character arrived, which is
strictly stronger than a human glancing at a screen. `conflict` and
`unverifiable` are equally definite: one is a proven failure to land as
intended, the other says the destination could not be read at all, so delivery
stayed unproven in either direction.

What passive evidence is, is **different**. So it is recorded as a distinct
third answer, `not-asked-machine-observed`, never as one of the human verdicts.
Every artifact and every summary keeps the two apart — a session holding one
attested app and four observed ones reads:

```
evidence scope: mixed-operator-attested-and-runtime-observed
operator-attested cases: 1 · machine-observed cases: 4 (131 utterances, no operator asked)
operator and runtime both clean: 1 · disagreements: 0 · not comparable (no operator verdict): 4
```

A passively observed app can never be silently promoted into an attested one.
If you have already answered for an app — recorded it or blocked it — `observe`
leaves that answer completely alone and says so.

## What passive observation does cover

### The app matrix (ledger 26, 27, 28)

Every insertion into an app you can name becomes a case: outcome states,
receipt reasons, readback shapes, capability buckets, aggregated across every
utterance rather than keeping only the newest.

**Its honest limit is that it only covers the apps you actually dictate into.**
That is usually a handful, and it is why the summary names every category with
no evidence at all:

```
categories with no evidence at all: browser, electron-chromium, ide, mail,
messaging, native-cocoa, notes, office, terminal, web-text-area
```

Read that as a to-do list. If you want terminal coverage, dictate into a
terminal once; if you want Office coverage, dictate into Word once. Ten seconds
of deliberate use fills a category that would otherwise stay empty forever.

An app that is not on the curated fifty still counts as a real app exercised —
it received real text — but it fills no planned slot, and the two numbers are
reported separately. The 50-app claim flips at fifty distinct apps and not one
app sooner. Nothing is ever extrapolated.

Two cases are skipped rather than guessed. An utterance with no id cannot be
de-duplicated, so it is not counted. And on Windows the runtime writes a
*window title* where macOS writes a bundle id; a window title can carry a
document name, so it is withheld — which leaves no app to attribute the case
to, and the utterance is skipped.

### Part of the lifecycle suite (ledger 16)

Three of the five scenarios leave a signal the runtime wrote down itself:

| Scenario | Signal | What it still cannot see |
|---|---|---|
| `long-form` | a continuous hotkey hold of at least three minutes | memory growth and thermal behaviour |
| `back-to-back` | consecutive utterances under five seconds of idle apart | how many utterances you actually spoke |
| `process-restart` | runtime start traces with utterance traces on both sides | nothing — both halves are in the log |

**Two scenarios leave no signal whatsoever.** `sleep-wake` and
`audio-device-switch` both run through `MacAudioRecoveryNotifications`, which
deliberately logs nothing at all, because device details must not reach a
routine log. A Mac that slept and a Mac that sat idle produce byte-identical
artifacts; so do a device switch and no device switch. Passive observation
cannot even tell that they happened, so it refuses to guess and names the
reason instead of quietly reporting zero.

**Passive lifecycle observation discharges nothing.** All three
`requires_physical_validation` ids need a human to confirm something no log
contains — that the Mac really slept, that the input really changed device, or
how the machine behaved thermally through a long capture. `observe` records
what the runtime evidenced and leaves every id still required:

```
discharges: nothing yet (basis: operator-attested-runs-only)
still required: physical-audio-device-switch, physical-long-audio-memory-thermal,
                physical-operating-system-sleep-wake
```

That is not a limitation to route around. It is the whole point of the
distinction.

## What passive observation cannot serve, and why

Three corpora are not merely harder to collect passively — they are
**impossible** to collect passively, because ordinary use does not produce the
kind of data they need. Use `scripts/capture_voice_evidence.py` and
`scripts/capture_delayed_cleanup_cases.py` for these. See
[`voice-corpora.md`](voice-corpora.md) and
[`physical-sessions.md`](physical-sessions.md).

### Selective Re-listen — ledger 18/19

Needs 20 **confirmed** and 20 **contradicted** cases, where "contradicted"
means you deliberately said something different from what the manifest claims,
and the verifier must catch it.

Passive use has **no ground truth**. Nothing in your dictation history records
what you actually said — only what the recognizer thought you said. Without
knowing the true utterance there is no way to label a case confirmed or
contradicted, and a corpus of unlabelled cases measures nothing. Worse, the
contradicted half — the half that matters, the one that stops a wrong invoice
number being inserted — cannot occur by accident at all. You have to say the
wrong thing on purpose.

### Acoustic calibration (ledger 15) and keyword priority (ledger 22)

Both need **paired arms**: the same utterance run through a baseline
configuration and a candidate configuration, under conditions held otherwise
constant, so the difference between them is attributable to the change.

Ordinary use produces exactly one arm. You dictate once, under whatever
configuration was live, in whatever room you were in. There is no second run
of that same utterance to compare against, and there never will be. A
one-armed comparison is not a weak measurement, it is not a measurement.

### Delayed cleanup — ledger 25

Needs specific drift scenarios present **in balance** — each failure mode
represented enough times that the evaluator can distinguish a real pattern from
a run of luck.

Some of these do occur naturally: focus drift and surrounding-text drift show
up in real use, and passive observation will faithfully record them. But
"occurs sometimes" is not "occurs in balance". You cannot arrange for a
specific drift to happen a specific number of times by dictating normally, and
a corpus that is 90% one failure mode and 3% another cannot support the claim
the evaluator is being asked to check. The balance requirement is not
satisfiable by chance.

## Where things land

Session state and artifacts go to `.evidence/`, which is gitignored and written
owner-only (`0600`), exactly as the guided sessions do. Artifacts are
transcript-free: they carry app identity, outcome and capability enums, readback
*shapes*, and counts. They never carry dictated words, window titles, file
names, tone-preset names, or the routing path.

Passive observation accumulates. The app matrix de-duplicates on the transcript
id and keeps its running totals in the session, so history the runtime later
trims away is not lost. The lifecycle observation is a recomputation of what the
current logs show, because the runtime log carries no per-line identity — if the
log rotates, its counts describe the new window.

No tool here writes an activation receipt, passes a manual-review flag, or
turns a machine reading into a human answer.
