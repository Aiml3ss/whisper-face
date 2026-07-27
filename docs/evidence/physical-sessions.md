# Physical evidence sessions

Several ledger rows in [`docs/development-65.md`](../development-65.md) are
code-complete and deliberately claim nothing about the physical world. Rows 26,
27, and 28 ship a simulation that reports `real_apps_exercised: 0`. Row 25 is
fail-closed behind a receipt nobody has produced. Row 16 says the adapter
simulation is done and physical device-switch, sleep/wake, thermal, memory, and
long-audio evidence remains. Rows 2, 8, 11, and 34 need hardware or products
this repository cannot drive at all.

This page is the operator's guide to closing those gaps. Three sessions are
guided by a tool. Four are checklists, because nothing here should pretend to
automate a fresh Mac, a Windows box, someone else's laptop, or a competitor's
product.

Before booking a sitting, run
[`passive-observation.md`](passive-observation.md): part of the app matrix and
part of the lifecycle suite can be harvested from dictation you have already
done, for free, in a second. It also states plainly which corpora ordinary use
can never supply, so you only spend a sitting on evidence that actually needs
one.

Every session obeys the same rules:

- **Nothing is defaulted.** A value is recorded only when the runtime printed
  it or the operator picked it from a printed list. An unanswered case is
  recorded as blocked with a closed reason, never as a pass.
- **Artifacts are transcript-free.** They carry app identity, capability and
  outcome enums, and counts. They never carry dictated words, window titles,
  file names, or anything you had on screen.
- **Dictate the neutral phrase you are given, and nothing else.** Never dictate
  real work into an evidence session.
- **Sessions resume.** Stop whenever you like; re-run the same command and it
  picks up at the first unanswered case. An already-answered case is never
  silently rewritten.
- **No tool writes a receipt.** `delayed_cleanup_activation.py` is the only
  thing that may install an activation receipt, and only you may run it.
- **Measurement mode is not authority.** Where a session needs the runtime to
  do something only a receipt normally enables, start the runtime with an
  explicit `--measure` argument. It applies the real code path for one process
  session, writes nothing, ends when the process does, and is recorded in the
  evidence it produced. It never becomes a receipt and never relaxes a
  threshold.

Session state and artifacts land in `.evidence/`, which is gitignored and
written owner-only (`0600`).

## At a glance

| Session | Ledger rows | Tool | Realistic operator time |
|---|---|---|---|
| 50-app insertion matrix | 26, 27, 28 | `scripts/capture_app_matrix.py` | 2.5–3.5 h, best split into two sittings |
| Delayed-cleanup 50-case suite | 25 | `scripts/capture_delayed_cleanup_cases.py` | 3–3.5 h, runtime started in measurement mode |
| Lifecycle and stress | 16 | `scripts/capture_lifecycle_evidence.py` | 80–100 min |
| Fresh-Mac onboarding | 2 | checklist | 45–75 min plus a clean machine or account |
| Hardware matrix | 11 | checklist | 40–60 min per machine |
| Windows live verification | 34 | checklist | 60–90 min |
| Competitor task run | 8 | checklist | 30–45 min per product, plus a clean machine for the install task |

---

## Session 1 — the 50-app insertion matrix (rows 26, 27, 28)

**What it produces.** A matrix artifact whose per-app records carry the exact
`insertion_integrity.ReceiptState` and `ReceiptReason` the runtime reported,
plus one closed operator verdict per app, plus honest coverage. If you finish
31 apps the artifact says 31 and sets `fifty_app_claim: false`. It always sets
`four_nines_claim: false`: one attempt per app cannot support a rate claim of
any kind, let alone 99.99%.

**Before you start.**

1. Whisper Face is installed and running, and you have dictated at least once,
   so `transcripts.jsonl` exists next to `dictate.py`.
2. Accessibility and microphone permissions are granted.
3. Open a scratch document in every app you plan to test, so switching is fast.
4. Nothing private is on screen. You will be dictating a fixed neutral phrase
   into scratch documents only.

**Run it.**

```sh
uv run scripts/capture_app_matrix.py plan
uv run scripts/capture_app_matrix.py export-apps --out .evidence/my-apps.json
uv run scripts/capture_app_matrix.py --apps .evidence/my-apps.json run
uv run scripts/capture_app_matrix.py --apps .evidence/my-apps.json emit \
  --out .evidence/app-matrix.json
```

Edit `.evidence/my-apps.json` first: delete the apps you do not have and add
the ones you do. Keep the `category` values from the built-in list — they are
what the coverage display and the artifact count. Changing the list changes the
plan digest, so finish a session on the list you started it with.

**What the tool reads.** Only the transcript-free keys of `transcripts.jsonl`:
`metrics.insertion_state`, `metrics.insertion_reason`,
`metrics.paste_attempted`, `metrics.insertion_verified`, `metrics.insertion_s`,
and whether the routing label began with `outbox/`. It never reads `raw`,
`clean`, or `observed_text`. It reads `app` only when it is a macOS bundle
identifier; on Windows that field holds a window title, so it is withheld.

**What the tool asks you.** Two closed questions per app: what actually
appeared on screen, and how the app itself behaved. `could-not-judge` is a
valid answer and blocks the case rather than scoring it.

**What "balanced" means here.** Not applied/rejected balance — that is the
delayed-cleanup gate. Here balance means *surface-class* balance. A matrix of
40 Cocoa text views proves nothing about Electron. Aim for at least:

| Category | Minimum for a credible claim |
|---|---|
| `native-cocoa` | 6 |
| `electron-chromium` | 6 |
| `browser` | 4 |
| `web-text-area` | 4 |
| `terminal` | 4 |
| `ide` | 4 |
| `office` | 3 |
| `messaging` | 3 |
| `notes` + `mail` | 3 combined |

The progress line shows exactly this, e.g.
`31/50 · electron-chromium 4/8 · terminal 2/5`.

**Time.** About three minutes per app once you have the scratch documents open:
switch, focus, dictate, wait, judge, answer. Fifty apps is 2.5 hours of
answering plus roughly half an hour of setup and app-launching. Two sittings of
25 apps is more accurate than one of 50, because judgement quality drops.

**Capability buckets are now exported.** `commit_insertion` computes the
capability triple `compatibility_fingerprint.CompatibilityObservation` wants —
`target` (`readable`/`opaque`/`unavailable`), `paste`, `readback` — and writes
it to `transcripts.jsonl` as `metrics.insertion_target`,
`metrics.insertion_paste`, and `metrics.insertion_readback`. A session recorded
against a current runtime therefore carries both halves of every observation
and reports `capability_buckets_available: true`, so the artifact can feed the
fingerprint aggregator directly.

Utterances the runtime had no lease for (the legacy paste path) still report
the outcome half only, and say so, rather than guessing a bucket. Sessions
recorded against an older runtime behave the same way.

---

## Session 2 — the delayed-cleanup 50-case suite (row 25)

**Start the runtime in measurement mode.** Delayed cleanup schedules a pass
only when a valid activation receipt is installed — the receipt this session
exists to earn. So run the whole session with the runtime started like this:

```sh
launchctl bootout gui/$UID/com.berg.dictate      # stop the installed service
uv run --locked --script dictate.py --measure delayed-cleanup
```

Stop the LaunchAgent first. Whisper Face holds a single-instance lock, so a
second copy started with `--measure` exits immediately and silently while the
ordinary one keeps running — you would record a whole session of blocked cases
without an obvious reason. Bring the service back with
`launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.berg.dictate.plist`
when the session is over.

Measurement mode schedules the same real transaction through the same
`DelayedCleanupTransactionAdapter` against the same destination. It is not a
receipt: it writes nothing, `measurement_mode.py` cannot produce one, and it
ends when you quit the runtime. It announces itself with a `[measurement]`
banner at startup and a menu-bar row while it is on, and it marks every
delayed-cleanup line it produces. The capture tool reads that mark from the
runtime's own line — it never asks you and never assumes — and carries it into
each record as `measurement_mode`, so the receipt reports how many of its 50
cases were measured.

The gate **accepts** a measured corpus. It measures the shipping path, which is
the point; the label is what keeps it distinguishable, not disqualifying.

Without either a receipt or `--measure delayed-cleanup`, the runtime prints no
`[delayed-cleanup]` line and every case blocks on `no-runtime-line`.

**Run it.**

```sh
uv run scripts/capture_delayed_cleanup_cases.py plan
uv run scripts/capture_delayed_cleanup_cases.py run
uv run scripts/capture_delayed_cleanup_cases.py summary
uv run scripts/capture_delayed_cleanup_cases.py emit \
  --out delayed_cleanup_physical_cases.json
```

**The plan.** Fifty cases on a four-surface by four-scenario grid: three per
cell, plus two extra so the total reaches fifty, leaving surfaces on 13/13/12/12
and scenarios on 13/13/12/12. That clears every floor in
`delayed_cleanup_activation.py`: 50 cases, 10 per surface, 8 per scenario.

**Where the fifth scenario went.** `duplicate-callback` used to be required and
was reachable by no operator action: `dictate.py` derives the proposal id from
the per-utterance `event_id`, so two delayed passes never share one and the
adapter's in-flight and completed-duplicate paths cannot be entered from the
shipped runtime. It is dropped from the gate rather than made reachable by
injecting a duplicate id, which would have been a synthetic condition wearing a
physical label. `tests/test_delayed_cleanup_merge.py` covers the single-use-id
contract deterministically instead.

**Where `apply_ms` comes from.** The runtime times the transactional apply — the
destination read, merge, and conditional write — and appends it to its own line
as `; <float> ms`. The proposal build is deliberately excluded: it is cleanup,
not apply, and the 150 ms budget is an apply budget. A pass that never reached
an apply (`proposal_failed`, or an adapter exception) prints no duration and the
case blocks on `no-runtime-timing`. Nothing is defaulted or stopwatched.

**What "balanced" means here.** The gate needs at least 15 cases whose observed
outcome is `applied` and at least 15 whose outcome is anything else. The plan
predicts 25 applied (`unchanged` and `edit-elsewhere`) and 25 rejected
(`edit-overlap`, `focus-drift`). Those predictions come from
`DelayedCleanupTransactionAdapter._apply_once`: an operator edit lands
before the first snapshot, so an edit *inside* the dictated span rejects every
proposal edit and yields `no_safe_changes`, while an edit *away* from it still
merges. Predicting is the point — a mismatch between prediction and observation
is a failure the gate must catch, so never adjust a prediction after seeing a
result.

**Dictate the disfluent phrase the tool prints.** A clean phrase gives the
delayed pass nothing to propose, and you get `proposal_failed` or
`no_safe_changes` no matter how the destination behaves.

**When the suite is complete**, review every case yourself — that is what
`--manual-reviewed` attests — and then run the command the tool prints:

```sh
uv run delayed_cleanup_activation.py delayed_cleanup_physical_cases.json \
  --manual-reviewed --write-receipt delayed_cleanup_activation.json
```

The capture tool never runs this, never writes the receipt, and never sets
`--manual-reviewed`. That flag is your personal attestation.

Then restart Whisper Face **without** `--measure`, so what runs afterwards is
the approved receipt rather than the session override.

**Time.** About 3.5 minutes per case: set up the surface, dictate, perform the
drift action inside the window, wait for the pass, answer four safety
questions. Fifty cases is roughly three hours, plus setup. Do it in blocks of
one surface class at a time so you are not re-arranging windows constantly.

---

## Session 3 — lifecycle and stress (row 16)

**What it produces.** A content-free artifact using the same five scenario
names as `performance_lab.py lifecycle` — `long-form`, `back-to-back`,
`process-restart`, `sleep-wake`, `audio-device-switch` — so the physical run
and the simulation can be read side by side. It also reports which of the three
`requires_physical_validation` ids the simulation emits are now discharged:
`physical-long-audio-memory-thermal`, `physical-operating-system-sleep-wake`,
and `physical-audio-device-switch`.

**Run it.**

```sh
uv run performance_lab.py lifecycle --format json   # the simulation, for contrast
uv run scripts/capture_lifecycle_evidence.py plan
uv run scripts/capture_lifecycle_evidence.py run
uv run scripts/capture_lifecycle_evidence.py emit --out .evidence/lifecycle.json
```

**The sixteen runs.**

| Scenario | Runs | Physical action |
|---|---:|---|
| `long-form` | 3 | one continuous dictation of at least three minutes |
| `back-to-back` | 3 | five utterances with no pause between them |
| `process-restart` | 3 | `launchctl kickstart -k gui/$UID/com.berg.dictate` between two dictations |
| `sleep-wake` | 3 | sleep at least sixty seconds, wake, unlock, dictate |
| `audio-device-switch` | 4 | plug or unplug wired headphones, or connect or disconnect AirPods, between two dictations |

**What the tool reads.** The count and insertion states of the utterances the
runtime logged during the run, how many were diverted to the Voice Outbox, and
how many `[audio] capture ready` lines the process printed while re-opening a
stream. **What it asks you.** Whether every utterance you spoke produced text,
how the runtime came back after the action, and how the machine itself behaved
— that last one is the thermal and memory evidence the ledger asks for.

**Have both kinds of audio device to hand.** Do at least two switches in each
direction. A switch that only ever adds a device does not prove the runtime
survives losing one.

**Time.** About 80 minutes. The long-form runs alone are 10 minutes of talking,
and the sleep/wake runs need a real minute of sleep each. Budget 100 minutes if
you are also watching Activity Monitor for memory growth.

---

## Checklist A — fresh-Mac onboarding walkthrough (row 2)

The ledger says the AppKit gate exercises every onboarding transition and that
"a physical fresh-Mac walkthrough remains". Nothing scripts this: the whole
point is a machine that has never granted this app anything.

**You need** a Mac that has never run Whisper Face — a freshly wiped machine, a
brand-new macOS user account, or a fresh VM. A new user account is the cheapest
honest option; permissions and LaunchAgents are per-user.

Record, for each step, exactly one of: `observed-as-described`,
`observed-differently`, `did-not-appear`, `blocked`. Write down what you
actually saw for anything that is not `observed-as-described`.

1. Clone the repository and run `./Install.command`. Record whether it
   completes without a single undocumented step.
2. On first launch, record whether the app asks for **microphone** access, and
   whether the panel shows the Whisper Face icon and name.
3. Record whether it asks for **Accessibility** access, and whether the
   Overview's status changes on its own after you grant it, without a restart.
4. From Overview, use the one accessible action that opens System Settings.
   Record whether it opens the generic pane without changing a grant, and
   whether the visible status refreshes when you come back.
5. Record the **model** step: which model is downloaded, how long it takes, and
   whether the status is accurate while it downloads.
6. Complete the **hotkey practice** step. Record whether it completes only
   after a capture is actually observed, not merely after a keypress.
7. Perform your **first dictation** into TextEdit. Record whether the intended
   text appears, and how long from first launch to that moment.
8. Run the whole thing again with VoiceOver on. Record whether every Overview
   control has a label that says what it does, and whether Tab order reaches
   all of them.
9. Restart the Mac. Record whether dictation works after login without any
   manual step.
10. Record the completion acknowledgement: does the app tell you onboarding is
    done, and does it stay done after a relaunch?

Report the total wall-clock time from clone to first successful dictation. That
number is the row's real evidence.

**Time.** 45–75 minutes, longer on a slow network because of the model
download.

---

## Checklist B — hardware matrix (row 11)

"Reproducible runs on representative real hardware." Representative means at
least: one Apple Silicon laptop on battery, one Apple Silicon desktop or
laptop on power, and one Windows machine. If you have more than one Apple
Silicon generation, use both — thermal behavior is the thing that differs.

Per machine, record the machine class (`apple-silicon-laptop-battery`,
`apple-silicon-laptop-power`, `apple-silicon-desktop`, `windows-x64`,
`windows-arm64`), the chip family, the RAM, and the OS version. Do not record
serial numbers or host names.

Then, on each machine:

```sh
uv run performance_lab.py startup --trace-log dictate.log
uv run performance_lab.py warm-path --trace-log dictate.log
uv run performance_lab.py evaluate
uv run tests/test_installers.py
```

and record:

1. Install method used, and whether it completed unmodified.
2. Cold-start time to ready, from the runtime's own trace lines.
3. Warm-path p50/p95 from the same source, over at least 20 dictations.
4. Which ASR engine was selected, and whether it fell back.
5. Whether a long-form dictation (three minutes) completed without thermal
   throttling. On a laptop, do this twice: once on battery, once on power.
6. Peak memory during that long-form run.
7. Whether `./setup.sh --verify` (Mac) or `.\setup.ps1 --verify` (Windows)
   passes.

A row is only reproducible if two runs on the same machine agree. Run the
warm-path measurement twice, on different days, and record both.

**Time.** 40–60 minutes per machine after the install, plus install time.

---

## Checklist C — live Windows verification (row 34)

The ledger says `setup.ps1 --verify` proves task-to-launcher configuration but
not independently managed Ollama listener ownership, and that a real Windows
verify and first dictation remain.

**You need** a real Windows machine — not a VM sharing the Mac's audio stack,
because the point is the Windows audio path.

1. Clone the repository on Windows and run `Install.cmd`. Record whether it
   completes without a manual step.
2. Run `.\setup.ps1 --verify`. Record the full pass/fail line for each check:
   the private launcher receipt, the checkout and locked `dictate.py` command,
   the current-user task principal, the sole expected PowerShell action, the
   health check, and the model check.
3. Record whether the scheduled task runs as the current user and not as
   SYSTEM.
4. **The gap the ledger names:** determine who owns the Ollama listener.
   Record the listening port, the owning process, and the account it runs as.
   Record whether Whisper Face started it or attached to one that was already
   running. This is the check `--verify` cannot make for itself.
5. Perform a first dictation into Notepad. Record whether the intended text
   appears.
6. Repeat the dictation into a Windows Store app and into a terminal. Record
   each result.
7. Sign out and back in. Record whether dictation works again with no manual
   step.
8. Record the Windows build number and whether the machine is x64 or arm64.

**Time.** 60–90 minutes, plus install and model download.

---

## Checklist D — competitor task run (row 8)

The protocol is [`benchmarks/competitor_tasks.json`](../../benchmarks/competitor_tasks.json)
and the evaluator is `competitor_benchmark.py`. Neither runs a competitor's
product, and neither should: the tool validates observations you collected by
hand and computes descriptive aggregates. It emits no ranking and no winner.
See also [the neutral competitor evaluation page](../benchmarks/competitor-evaluation.md).

**The six neutral tasks**, in protocol order:

| `task_id` | What you do | Complete when |
|---|---|---|
| `fresh-install` | On a clean machine, follow only the product's public primary install path, grant what it asks, and dictate once | intended text first appears in the target app with no undocumented setup step |
| `ready-from-launch` | Launch the installed product and reach its documented ready-to-dictate state | the product visibly says dictation can begin |
| `short-message` | Dictate: *Please send the revised agenda before lunch.* | the target holds that sentence with equivalent capitalisation and punctuation |
| `numbers-and-units` | Dictate: *Set the sample rate to 48 kilohertz and the buffer to 256 frames.* | both quantities, units, and their associations are correct |
| `spoken-correction` | Dictate a short sentence, then use the product's documented correction flow to replace one wrong word | the replacement appears once and the sentence is otherwise intact |
| `two-sentences` | Dictate: *The prototype is ready for review. Please add comments by Friday.* | both sentences appear in order, nothing duplicated or missing |

**Per product, per task, record exactly one of:**

- `measured` — with `completed`, `error_count`, `latency_ms`,
  `interaction_count`, and a `source_reference` pointing at your artifact
  (a screen recording file name, a note id). An incomplete measured task needs
  at least one error.
- `unavailable` — with one closed reason (`environment_unsupported`,
  `not_run`, `observation_missing`, `product_unavailable`) and **no numbers**.
- `claimed_only` — with a `source_reference` to the vendor's own published
  claim and **no numbers**. A claim never enters a measured total.

Read the measurement definitions in the corpus before you start; they fix
exactly where the latency clock starts and stops, and exactly what counts as an
interaction (permission approvals count, spoken words do not). Use the same
hardware, the same microphone, the same room, and the same acoustic conditions
for every product, and record all of that in `environment_id`.

Every product run must cover **all six** tasks explicitly — a missing task is a
schema error, not an omission. Then:

```sh
uv run competitor_benchmark.py --protocol benchmarks/competitor_tasks.json \
  .evidence/whisper-face-run.json .evidence/other-product-run.json
```

**Time.** 30–45 minutes per product for tasks 2–6. The `fresh-install` task
needs a clean machine or a new user account per product; budget it separately,
around 45 minutes each, and never reuse a machine that already has the product
on it.

---

## What each artifact unblocks

| Artifact | Unblocks | Still blocked afterwards |
|---|---|---|
| `.evidence/app-matrix.json` | rows 26, 27 — real apps exercised, adversarial cases observed on real destinations, and both halves of every compatibility observation | row 28: a four-nines rate needs repeated trials per app, not one pass |
| `delayed_cleanup_physical_cases.json` | row 25, once 50 cases are recorded and you have reviewed every one | nothing in row 25 |
| `.evidence/lifecycle.json` | row 16's physical device-switch, sleep/wake, thermal, memory, and long-audio evidence | nothing in row 16, once all five scenarios are recorded |
| onboarding checklist notes | row 2's fresh-Mac walkthrough | nothing in row 2 |
| hardware matrix notes | row 11 | nothing, provided two runs per machine agree |
| Windows checklist notes | row 34's real verify and first dictation | the Ollama listener ownership question, which needs a runtime change to answer automatically |
| competitor run files | row 8's cross-product physical run | nothing in row 8 |
