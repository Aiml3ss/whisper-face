# Recording the voice corpora

Four features stay off until this Mac produces its own evidence: Selective
Re-listen (ledger 18/19), acoustic calibration (ledger 15), and pronunciation
keyword bias (ledger 22). The evaluators for all three already exist and are
strict. What was missing was a humane way to record the corpora they eat.

`scripts/capture_voice_evidence.py` is that. It prompts you through each case,
records what you actually say at 16 kHz mono, tracks balance as you go, and
writes a manifest the existing benchmark consumes with no hand-editing.

It has no authority. It never generates audio, never guesses an outcome label,
never runs a benchmark, and never writes an activation receipt. It prints the
exact command to run and stops. `--confirm-manual-review` stays yours to pass,
after you have listened.

## Before you start

```sh
cd ~/dictation
uv run scripts/capture_voice_evidence.py relisten --plan
```

`--plan` creates the session, prints every case, and exits without touching the
microphone. `--status` shows progress on an existing session. `--list-devices`
prints what the machine can hear with.

Everything lands in `.evidence/<corpus>/` — directories `0700`, files `0600`,
written atomically, and gitignored. The tool refuses to write anywhere inside
the checkout that `.gitignore` does not already cover. To keep the corpus off
the repository disk entirely, pass `--evidence-root ~/Private/whisper-evidence`.

Recording is macOS-only and refuses without a working 16 kHz mono input device.

## Selective Re-listen — about 30 minutes

40 cases, 20 confirmed and 20 contradicted, interleaved so a half-finished
session is still balanced. Each take is capped at **2.4 seconds**: the verifier
only ever sees a microspan and the benchmark rejects anything longer.

**Confirmed** means you say the phrase and the manifest expects that same
phrase. The verifier should agree with you.

**Contradicted** means you say a deliberately different value — "invoice 2043"
— while the manifest still expects "invoice 2042". The verifier is being asked
whether the audio supports the text it was handed, and here it must say no.
This is the case that matters: it is exactly the situation where a wrong number
would otherwise get inserted into a real invoice.

The tool shows both lines every time, labelled `SAY THIS OUT LOUD` and
`MANIFEST EXPECTS`, so there is no ambiguity about which one to read.

```sh
uv run scripts/capture_voice_evidence.py relisten
```

Per case: `[r]` record, `[p]` play back, `[a]` accept, `[s]` skip, `[q]` quit
and resume later. A live level meter shows peak and RMS while recording, and
warns on clipping or a take that is barely audible.

## Acoustic calibration — about 2 hours, best split across sittings

40 cases, at least 8 each of `clean`, `quiet`, `noisy`, and `long-pause`, and
each case needs a **baseline** and a **candidate** outcome. The session runs as
two passes over the same task set so you only change settings once.

It also needs at least eight real `utterance_acoustic` telemetry records. Import
them from your own log; nothing here will invent them:

```sh
uv run scripts/capture_voice_evidence.py calibration --telemetry-log ~/dictation/dictate.log
```

The tool prints the candidate front-end settings the existing policy derives
from that telemetry (gain ceiling, noise gate, VAD threshold, end silence).

For each case you dictate the sentence into Whisper Face for real, record a
witness take here so you can review it later, and answer two questions about
what you just observed:

- did it recognize the words correctly?
- did it end the utterance at the right moment?

Answer for the run you actually saw. There is no default and nothing infers
these.

**A wrinkle worth knowing.** Runtime applies calibrated gain, noise gate, VAD,
and end-silence values *only* from an approved receipt, so there is no supported
way to run the candidate pass before the receipt exists. To measure the
candidate arm honestly you have to apply the printed values locally for the
duration of that pass and put them back afterwards. Record what you changed.
This is a circularity in the gate, not something the capture tool can fix, and
it is worth resolving upstream before anyone runs the full corpus.

Use `--witness first` to record audio only in the baseline pass if the second
pass is taking too long; the labels still cover both arms.

## Pronunciation keyword — about 2 hours

One keyword per session. 40 records: 20 positive sentences containing the hard
name, 20 negative sentences containing a near miss that is *not* the name.

```sh
uv run scripts/capture_voice_evidence.py keywords --keyword Qwen --near-miss Gwen
```

The candidate must already be eligible in `acoustic_keyword_memory.json` — three
exact corrections and two confirmations, earned during real dictation. The tool
checks and reports that read-only at startup. It does not and must not write
that file: manufacturing eligibility would defeat the point of it.

Two passes again. The **unbiased** pass runs with the keyword absent from the
Whisper prompt; the **biased** pass runs with it present. The supported way to
get it into the prompt before activation exists is to add the term to
`dictionary.txt` above the managed marker for the duration of the pass. That is
an approximation of the activated behaviour, not the activated behaviour itself
— note it in your own records.

Per case per pass, two questions:

- did the keyword appear anywhere in the recognized candidates?
- was it the term actually selected into the text?

"Selected but not a candidate" is impossible and the tool will not let you
record it.

Negatives are the safety half: if the biased prompt starts hearing the keyword
in sentences that never contained it, that is a regression and the gate will
refuse — correctly.

## Reviewing, and then approving

Reviewing is the gate. Nothing in this tool has reviewed anything for you.

```sh
uv run scripts/capture_voice_evidence.py relisten --review
```

That replays every completed take next to its designed label so you can check
each one by ear. When the corpus is complete and you have listened to all of it,
the tool prints the exact three-step sequence: evaluate, review, then approve by
hand with `--approve-runtime` and `--confirm-manual-review`.

Approval writes a mode-`0600` receipt at the repository root. It is gitignored,
content-free, and bound to this machine's pinned model, policy thresholds, and
evidence.

## These receipts do not travel

A receipt is device-specific evidence that *this* Mac, with *this* microphone, in
*these* rooms, produced measurable results. Copying one to another machine
converts real evidence into a lie about a machine that never ran the corpus. It
is the exact failure the whole fail-closed design exists to prevent.

Re-record after any change to the verifier model, receipt schema, or policy
thresholds. Never copy audio, manifests, or receipts into Git, into a support
bundle, or onto another machine.
