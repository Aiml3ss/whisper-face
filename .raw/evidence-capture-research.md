# Research brief: evidence capture, gate defects, and first releases

Codebase research over the Whisper Face repository covering the second
half of what shipped on 2026-07-26 after the first wiki build: the two
evidence-capture harnesses (#107, #109), the two circular-gate defects
they surfaced (issues #108 and #110), and the three tagged releases plus
the repository's public state. Verified against the tree at commit
`1165335`; where a commit message and the code disagree, the code is
recorded and the disagreement is called out.

## 1. Why the harnesses exist

Several ledger rows had been code-complete for weeks while making no
physical claim, because nothing real had been exercised. The evaluators
existed; what did not exist was a humane way to record what they eat —
dozens of 16 kHz WAVs plus hand-authored JSON manifests, or fifty
app-by-app insertion observations. Rows 26-28 shipped a simulation
reporting `real_apps_exercised: 0`; row 25 was fail-closed behind a
receipt nobody had produced; row 16 still wanted physical device-switch,
sleep/wake, thermal, memory, and long-audio evidence.

Two commits answered that: `697613f` (#107) added
`scripts/capture_voice_evidence.py`, and `b6c346e` (#109) added
`scripts/capture_app_matrix.py`,
`scripts/capture_delayed_cleanup_cases.py`,
`scripts/capture_lifecycle_evidence.py`, and the shared
`scripts/capture_session_support.py`. Both added exactly one `.gitignore`
rule between them — `.evidence/` — with a comment naming all four tools.
(The receipt filenames and `delayed_cleanup_physical_cases.json` were
already ignored by earlier work.)

## 2. `capture_voice_evidence.py` — the three voice corpora

1,746 lines. `CORPORA` (:328-332) maps exactly three specs:
`relisten` ("Selective Re-listen (ledger 18/19)", :236), `calibration`
("Acoustic calibration (ledger 15)", :257) and `keywords`
("Pronunciation keyword bias (ledger 22)", :292). Each is an argparse
subcommand.

- **Recording.** `SAMPLE_RATE_HZ = 16_000`, `CHANNELS = 1`,
  `DTYPE = "float32"` (:65-67), enforced four times over: at
  `check_input_settings` (:1076-1089), at stream creation (:1118-1125),
  at WAV write (:467-481) and at readback, which raises "is not mono
  16 kHz PCM16" (:494-495).
- **The meter.** `meter_line()` (:1025-1038) draws a 28-cell bar with
  peak, RMS, elapsed against the cap, and flags `CLIPPING` at peak ≥0.99
  or `very quiet` below 0.05.
- **Storage.** `DIRECTORY_MODE = 0o700`, `FILE_MODE = 0o600` (:82-83);
  `atomic_write_bytes` is mkstemp → fsync → chmod → `os.replace`
  (:424-440). The session directory is `<root>/<corpus>` under
  `.evidence/` (:1521-1522, :1616), and `private_destination_error()`
  (:369-397) refuses any destination inside a Git checkout that the
  checkout's own `.gitignore` does not cover — it parses the real
  `.gitignore` at :345-358.
- **Resumption never overwrites.** `Session.record_arm` (:719-722)
  refuses a case already captured — "use `--redo` to reopen it
  deliberately" — with the docstring "Resuming a session must never
  quietly discard a case a human already spoke and reviewed". A case
  whose WAV has since vanished is reopened rather than shipped
  (:675-685).
- **The 2.4 s microspan cap.** `RELISTEN_MAX_SECONDS = 2.4` (:71) is
  pinned to the adapter's own bound —
  `whisper_verifier_adapter.MAX_AUDIO_SECONDS = 2.4` — and enforced twice
  during recording (hard stop :1138-1140, truncation :1148-1150) so a
  corpus cannot be rejected at load after the operator's time is already
  spent. A test asserts the constant still equals the adapter's.
- **Two passes over one task set.** Arms are `("take",)` for re-listen,
  `("baseline", "candidate")` for calibration, `("unbiased", "biased")`
  for keywords (:244, :265, :300). `active_arm()` (:696-700) returns the
  first arm with pending cases and the session loop finishes a whole pass
  before advancing, so the operator changes settings once per pass rather
  than once per case. Plans interleave (`confirmed-NN` /
  `contradicted-NN`) so balance is visible mid-session, and balance
  floors are checked before the manifest is written.

### The tool holds no authority — and that is structural

`tests/test_capture_voice_evidence.py` parses its own subject with
`ast.parse` at import (`TREE`, :47) and asserts, in
`class NoApprovalAuthorityTests` ("The capture tool must be structurally
incapable of approving anything.", :646):

- **never generates audio** — walks every `ast.Call`, asserts none of 13
  signal generators (`math.sin`, `np.random.rand`, `np.linspace`,
  `random.uniform`, `secrets.randbits`, …) appears, then finds the
  `record_take` function definition and asserts `sounddevice.InputStream`
  is among *its* calls (:763-788);
- **never imports an activation module** — `imported_modules()` walks
  `ast.Import` / `ast.ImportFrom` and intersects against a nine-name
  `FORBIDDEN_MODULES` frozenset (:690-694);
- **never executes another process** — walks every call, resolves dotted
  names, and matches a twenty-name `FORBIDDEN_CALLS` frozenset
  (`eval`, `exec`, `os.system`, `os.exec*`, `subprocess.*`, `runpy.*`, …)
  collecting `(name, lineno)` offenders (:696-704);
- **cannot even declare the approval flags** — walks every
  `add_argument` call scanning its constant arguments for
  `--confirm-manual-review` / `--approve-runtime`, asserts none is
  declared, then asserts every occurrence of the string in the source is
  inside a string literal (:717-740).

*Precision note.* Two of the five advertised properties are **not** AST
checks: "never writes a receipt" is a name plus `hasattr` check
(:706-715) and "never infers an outcome label" is a constant-identity
plus substring check (:742-747), backed behaviourally by
`test_labels_are_required_and_never_defaulted` against the raise at
:723-726 ("needs its outcome labels; nothing infers them"). The commit
message's "Tests assert those properties structurally over the AST" is
defensible but over-broad if quoted as covering all five.

The same discipline covers the other three harnesses through parallel
`NoReceiptWritingTests` classes that parse *both* their own script and
`capture_session_support.py`, asserting that `manual_reviewed`,
`activation_receipt` and `write_receipt` never appear as any bound name,
keyword, argument, or function name. The delayed-cleanup file adds
`test_the_gate_module_is_imported_for_constants_only`, which asserts the
set of names imported from `delayed_cleanup_activation` equals exactly
eight thresholds — the gate is imported for its numbers, never for its
writer. The 21 structural tests pass on this worktree.

### Where authority actually lives

`render_next_commands()` (:1243-1283) prints a three-step block headed
"NEXT STEPS - this tool stops here, on purpose": evaluate, review, then
approve. `--confirm-manual-review` is declared by
`benchmark_relisten_activation.py` (:451),
`benchmark_acoustic_calibration_activation.py` (:185) and
`benchmark_acoustic_keyword_activation.py` (:77) — never by a capture
tool, which only ever prints the string. `--review` *is* a capture flag
(:1643-1645) and replays every completed take next to its designed label,
printing "Nothing is approved here; this is only playback." (:1447-1448).
Manifest compatibility is proved end-to-end by tests that load capture
output into the real evaluators (:121, :157, :214).

## 3. The three physical-session harnesses

`scripts/capture_session_support.py` is the shared, import-only plumbing
(its docstring: "This module reads no receipts, writes no receipts, and
never sets a manual-review flag."). It provides atomic 0600 JSON writes
(:107-132), a resumable `Session` whose `record()` and `block()` both
raise on an already-answered case and which refuses a session file
belonging to another tool or a different plan digest (:138-221), and
`ask_choice()` (:234-262) — the closed-choice prompt that returns only a
listed key, re-prompts on anything else, and treats empty input as an
abort rather than a default. `wait_for_enter()` reads a line and discards
its content: a barrier, not an input. A vocabulary-integrity assertion
runs at *import* time (:498), so drift between the local `ReceiptReason`
and `compatibility_fingerprint.REASON_BUCKETS` fails the import of all
three tools.

Its `TranscriptReceipt` projection (:292-397) is a strict allowlist over
`transcripts.jsonl`: it reads the runtime's own transcript-free receipt
keys and never `raw`, `clean`, or `observed_text`. The routing `path` is
reduced to a single boolean because it embeds the operator's tone-preset
names, and the `app` field is withheld entirely when it starts with
`windows:` — on Windows that field holds a window title, not a bundle id.

- **`capture_app_matrix.py`** walks a curated fifty-app plan
  (`FIFTY_APP_TARGET = 50`, :70) across exactly ten surface classes
  (:73-84: native-cocoa 8, electron-chromium 8, ide 6, web-text-area 6,
  browser 5, terminal 5, office 4, messaging 4, mail 2, notes 2). It
  reads the runtime's own insertion receipt and asks the operator the one
  thing the machine cannot know — did the intended text land in the
  intended place — as a closed choice. *Correction to the commit
  message's shorthand*: there are up to **three** closed questions per
  app (availability, a seven-option text verdict, a five-option app
  behaviour); `docs/evidence/physical-sessions.md:89` describes it as
  "two closed questions per app", counting the two that are scored.
  Coverage is reported as measured: `"extrapolated": False` is hardcoded
  (:556), `real_apps_exercised` is the recorded count, and
  `"four_nines_claim": False` is unconditional (:570-577).
- **`capture_delayed_cleanup_cases.py`** walks the four-surface by
  five-scenario grid the activation gate demands, importing `SURFACES`
  and `SCENARIOS` from `delayed_cleanup_activation` so the gate stays the
  single source of truth (:65-74, validated :207-238). The built plan is
  50 cases — native-text 13, web-text 13, electron-editor 12,
  terminal-editor 12; ten per scenario; expected outcomes applied 20,
  no-safe-changes 10, focus-drift 10, proposal-in-flight 10. (A cosmetic
  defect: the `build_plan` docstring at :188-189 says the surfaces land
  "13/12/13/12"; the real distribution is 13/13/12/12, which
  `physical-sessions.md:168` states correctly.) It reads the runtime's
  own `[delayed-cleanup]` line via a regex with an *optional* `apply_ms`
  suffix (:86-89), asks four closed safety questions per case
  (wrong-target write, user edit overwritten, selection disrupted,
  duplicate write), and prints the activation command only when no gate
  shortfall remains — followed by "This tool does not run it, does not
  write the receipt, and does not set --manual-reviewed on your behalf."
- **`capture_lifecycle_evidence.py`** guides exactly the five scenario
  keys `performance_lab.run_lifecycle_simulation` reports — `long-form`,
  `back-to-back`, `process-restart`, `sleep-wake`,
  `audio-device-switch` (:69-72, verified against
  `performance_lab.py:1034-1038`) — across sixteen runs, and reports both
  which of the three `requires_physical_validation` ids a session
  discharges and which still stand (:76-80, :381-382). The artifact
  records its own provenance string,
  `"scenario_vocabulary": "performance_lab.run_lifecycle_simulation"`.

**Nothing is defaulted.** A case the runtime did not report, reported
ambiguously, or reported without an integrity receipt is recorded as
blocked with a reason from a closed frozenset — `no-runtime-record`,
`ambiguous-runtime-records`, `runtime-reported-no-receipt`,
`operator-could-not-judge`, `no-runtime-line`, `runtime-outcome-unknown`,
`no-runtime-timing`, `delayed-cleanup-inactive`, `hardware-unavailable`,
`operator-skipped` — validated on write. `TranscriptReceipt.has_receipt`
is true only when both the insertion state and reason are real enum
members; the runtime's no-receipt sentinels (`legacy`,
`unsupported_field`) are deliberately outside those enums, so a case
reporting them cannot be recorded as evidence of anything.

**No free text reaches an artifact** in the three session tools: every
answer goes through `ask_choice`, the fixed dictation phrases live in
source and only their *ids* are written, and poison-string tests inject
raw/clean/tone text and assert none survives into the artifact JSON.
(Scope note: `capture_voice_evidence.py` predates the shared library and
does not use it. Its prompts are closed too, but `--keyword` and
`--near-miss` are free-text CLI arguments that reach the keyword
manifest, and the corpus *is* recorded speech by design.)

**`docs/evidence/`** holds exactly two operator guides:
`voice-corpora.md` (157 lines — the three corpora with realistic time
budgets of ~30 min / ~2 h / ~2 h, the `.evidence/` privacy model, the
review-then-approve three-step, and a closing "These receipts do not
travel") and `physical-sessions.md` (430 lines — five shared rules, the
three sessions, four hardware checklists, and a closing "What each
artifact unblocks" table). Checklist A is the fresh-Mac onboarding
walkthrough (row 2), B the hardware matrix (row 11, with an explicit "Do
not record serial numbers or host names"), C live Windows verification
(row 34), and D the six-task competitor run (row 8: fresh-install,
ready-from-launch, short-message, numbers-and-units, spoken-correction,
two-sentences, each scored `measured` / `unavailable` / `claimed_only`,
with "A claim never enters a measured total").

*One connection worth not making:* `benchmark_cleanup_latency.py` is
**not** a consumer of any harness. It is a semantic cleanup-quality
benchmark, unrelated to the delayed-cleanup activation gate; no capture
script or evidence doc references it.

## 4. Two circular gates, filed as issues #108 and #110

Both were found while building the harnesses, before any operator time
was spent. Both are open on `main` as of this brief.

### #108 — calibration and keyword priority cannot measure their own candidate arm

Two of the four activation gates require A/B evidence comparing a
baseline arm against a candidate arm, but the runtime only produces the
candidate behaviour *after* the receipt the A/B is meant to authorize.

- Calibration: `dictate.py` loads the calibrated gain / noise-gate / VAD
  / end-silence settings only from a valid
  `acoustic_calibration_activation.json` (`load_acoustic_calibration_
  activation` at :1277, feeding `ACOUSTIC_CALIBRATION_STATE["settings"]`
  and its four consumers at :1285-1317). The candidate recordings
  therefore cannot exercise the calibrated front end.
- Keyword priority: a biased term reaches the Whisper prompt only
  through `active_acoustic_keywords(ACOUSTIC_KEYWORD_ACTIVATION_FILE,
  keyword_memory)` (:5581-5582) → `GLOSS["active_keyword_hints"]` →
  the prompt (:10547). The biased arm cannot be measured without the
  receipt it is meant to justify.

Selective re-listen has no such circularity — its benchmark drives the
verifier directly, which is why that corpus is straightforwardly
recordable today. The workarounds `docs/evidence/voice-corpora.md`
documents (temporarily editing local settings; putting the term in
`dictionary.txt`) both make the operator hand-modify runtime state
mid-corpus — exactly the unwitnessed step these gates exist to prevent —
and neither produces evidence of the real shipping path. The proposed fix
is an explicitly-labelled measurement-only override that is recorded in
the manifest, applies the candidate settings, and grants no runtime
authority of its own.

The same audit filed four smaller findings: the 2.4 s microspan cap is
enforced in code but absent from `docs/selective-relisten-activation.md`;
calibration `telemetry` and `cases` are unlinked, so a stale log plus a
fresh corpus passes while ledger row 15 reads as though they are one body
of evidence; the runbook's example case token is sequential and therefore
correlatable across manifests where the harness uses
`secrets.token_hex(8)`; and keyword-memory eligibility (3 observations
plus 2 confirmations via real corrections) is separately unreachable and
only discovered at approval time.

### #110 — delayed-cleanup activation is unearnable

Three defects, all still present in the code:

1. **Bootstrap deadlock.** `schedule_delayed_cleanup` returns `False`
   unless `DELAYED_CLEANUP_STATE["active"]` (`dictate.py:8534`), which is
   only true when a valid `delayed_cleanup_activation.json` already
   exists — and that receipt requires 50 manually reviewed physical cases
   produced by the feature running. With no receipt the runtime never
   schedules a pass, prints no `[delayed-cleanup]` line, and every case
   in a capture session blocks.
2. **`apply_ms` has no source.** The gate requires per-case apply timing
   at p95 ≤ 150 ms, but `_run_delayed_cleanup` measures no duration and
   its log line carries only
   `f"{outcome}; {applied_count} applied, {rejected_count} held"`
   (`dictate.py:8514-8515`). Nothing in the runtime, the transcript
   record, or the adapter emits an apply duration, and an operator
   stopwatch is not evidence at a 150 ms threshold. The capture tool
   already parses an optional `; <float> ms` suffix, so one runtime line
   would make the corpus recordable; until then every case blocks on
   `no-runtime-timing` rather than defaulting to a guess.
3. **`duplicate-callback` is physically unreachable.** The gate demands
   ≥8 cases in that scenario, but `dictate.py:9825` passes the
   per-utterance `event_id` (`f"{time.time_ns():x}-{id(rec):x}"`) as the
   proposal id, so two passes never share one and the transaction
   adapter's in-flight and completed-duplicate paths cannot be reached by
   any operator action. The suggested fixes are to drop the scenario
   (deterministic single-use-id tests already cover it) or to provide a
   supported replay under the evidence-collection mode from defect 1.

(The issue cites `dictate.py:8505` and `:8438`; those line numbers
predate `7359baa`, which added 28 lines to the file. The behaviour is
unchanged.)

The same issue records a related blocker for the richer half of the app
matrix: `compatibility_fingerprint.CompatibilityObservation` needs
`target` / `paste` / `readback` buckets, and `dictate.commit_insertion`
computes them but writes them nowhere an external tool can read —
`runtime_status_snapshot()` is in-process only and the HTTP endpoint
exposes just `/`, `/health`, `/source`, `/license`. Three keys in the
`append_transcript` metrics dict would close it; the capture tool already
reads them when present, which is why it reports
`capability_buckets_available: false` with the closed reason
`runtime-does-not-report-target-paste-readback-buckets`. Four smaller
traps are filed alongside: `metrics["verified"]` is second-pass ASR
verification, not insertion truth (`metrics["insertion_verified"]`), and
`benchmark_voice_compiler.py` conflates them; the `app` field leaks a
window title on Windows; `performance_lab.recoveries` counts attempts
rather than proven recoveries; and `_LIFECYCLE_IDS` says
`compiler-restart` where the simulation reports `process-restart`.

`docs/evidence/physical-sessions.md:132-155` records all three
delayed-cleanup defects for the operator, with the honest framing that
running the session anyway "records precisely which blocker fired for
each case, which is the evidence that these are gate defects rather than
operator error."

**Status for the wiki: these are known blockers, not working features.**
A fix is in flight on another branch; nothing in this brief describes it.

## 5. Three releases, and a public repository

`gh release list` at the time of this brief:

| tag | published (UTC) | assets |
|--------|------------------|--------|
| `v0.1.0` | 2026-07-26T15:53:37Z | DMG 2,711,672 B · source ZIP · SHA256SUMS · update-manifest.json |
| `v0.2.0` | 2026-07-26T21:28:35Z | DMG 2,929,993 B · source ZIP · SHA256SUMS · update-manifest.json |
| `v0.2.1` | 2026-07-26T22:29:32Z | DMG 3,133,874 B · source ZIP · SHA256SUMS · update-manifest.json |

All three are published, none is a draft or prerelease, and every
download count is zero. The site offers exactly one of them through a
single constant, `site/src/data/release.ts` — "Update these four values
when cutting a release; every download link on the site reads from here."
It currently holds `version: '0.2.1'`, `tag: 'v0.2.1'`, the arm64 DMG
URL, `size: '3.0 MB'`, links to the release notes and `SHA256SUMS`, and
`unsigned: true`. Commits `59e5ffc` (#106) and `1165335` (#113) each
changed only that file, six lines each.

`Install.astro` is the only consumer: the primary CTA is
`href={RELEASE.dmg} download` with "{version} · {size}", the strapline
"Apple Silicon, macOS 14 or newer", and — gated on `RELEASE.unsigned` —
the honest Gatekeeper warning: "This build is not notarized yet", with a
pointer to `SHA256SUMS` and "A signed release is on the way." Every other
"Download" on the site is an anchor to `/#install`.

The repository is **public**: `gh repo view --json visibility,isPrivate`
returns `PUBLIC` / `false` for `Aiml3ss/whisper-face`, created
2026-07-21. Nothing in the tree describes the repository as private —
grepping the `.md`, `.astro`, `.ts`, `.py` and `.yml` files for
visibility language returns only unrelated uses of "private" (private
user files, private vulnerability reporting, 0600 private state).

*What could not be verified.* The session notes for the day say the
repository was private until today, which would explain why release
downloads had never worked for anyone. The repository's own event feed
carries exactly one `PublicEvent`, stamped `2026-07-21T01:09:09Z` — the
same instant as `createdAt` — so the visibility history is not
recoverable from the API, and this brief does not assert it. What is
recorded: the repository is public now, three releases carry real assets,
and every download count is still zero.
