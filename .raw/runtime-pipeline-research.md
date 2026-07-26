# Research brief: the dictation runtime pipeline

Codebase research over the Whisper Face repository (2026-07-26), covering
`dictate.py`, `parrot_core.py`, `voice_compiler.py`, the verifier stack,
delayed cleanup, the Windows path, and process boundaries. Line references
are against commit `b49699f`.

## Per-module responsibilities

**`dictate.py`** (10,801 lines) — the single-process runtime. It owns
everything with a side effect: platform imports and AppKit/pystray shims,
all tunable constants (:487-950), module-level singletons and mutable
runtime state, the AppKit HUD/WaveView/StatusBar (:2043-3510), audio
capture (Flight Recorder, AudioPool, Recorder) (:3508-4485),
glossary/learning (:4484-5970), the native Parakeet helper client
(:6232-6390), the ASR cascade (:6392-6635), the Voice Compiler adapter +
consequence/context-firewall evidence (:6759-7210), snippets (:7213-7400),
Accessibility focus reading, insertion lease/readback/commit (:7401-8140),
cleanup routing and LLM proof edits (:8256-8675), the phone endpoint
(:8676-9060), paste and spoken-command dispatch (:9064-9300), the
whole-utterance pipeline `finish_and_process` (:9507-10195), and `main()`
with the hotkey worker (:10451-10800). It is a shell module: nearly every
algorithm it uses is imported from a pure module.

**`parrot_core.py`** (654 lines) — pure, dependency-free core types and
deterministic text logic: `Recognition`/`RecognitionWord`/`CleanupPlan`/
`CleanupEdit`, Whisper prompt construction and context-term ranking,
phonetic keys and `correction_similarity`, the deterministic cleanup
compiler `compile_cleanup` and `compile_code_dictation`, the closed
spoken-edit-command grammar, segment-to-confidence/word conversion, the
modifier-to-mode table `mode_from_modifiers`, and the two rolling-ASR
predicates `should_start_speculation` / `can_reuse_speculation`.

**`voice_compiler.py`** (1,713 lines) — the deep module. Owns VoiceIR and
everything downstream of recognition evidence: context adapters/router,
`VoiceIR` and its receipt dataclasses, protected-anchor extraction, the
consequence risk taxonomy, `build_consequence_plan` /
`execute_consequence_plan` / `consequence_receipt`, the `VoiceCompiler`
class (fusion, prosody formatting, stable prefix, proof-edit validation),
the shadow `context_firewall_receipt`, and `analyze_prosody`. It never
touches I/O, the clipboard, or an application.

**`process_verifier.py`** (245 lines) — the provider-neutral killable
boundary. `ProcessIsolatedVerifier` forks a fresh process per request,
sends one `VerificationRequest`, and accepts exactly one
`{outcome, confidence, engine}` mapping under an absolute monotonic
deadline. Late responses are refused even if already queued. Refusals are
a closed enum: `timeout`, `crash`, `malformed-result`.

**`prewarmed_verifier.py`** (367 lines) — same contract, but one
long-lived child. `PrewarmedVerifierSupervisor` lazily starts a child that
calls a `provider_factory()` once, then serves requests sequentially over
a duplex pipe. `prewarm()` initializes the model without ever sending
audio or expected text. Any timeout, crash, or malformed message discards
the entire child. Bounds are enforced parent-side: sample rate exactly
16 kHz, at most 2,400 ms of audio, at most 160 expected characters.

**`whisper_verifier_adapter.py`** (463 lines) — the only module that names
a verifier model. Pins Whisper Tiny by repo + revision, normalizes both
sides (NFKC/casefold/letter-digit-only), derives confidence from
duration-weighted `exp(avg_logprob)`, and decides: below 0.55 confidence
or empty means inconclusive; exact normalized match means confirmed;
token-edit similarity at or below 0.5 means contradicted; else
inconclusive. Both verifier classes declare
`process_isolated=True, strict_deadline=True, retains_audio=False`.

**`delayed_cleanup_merge.py`** (462 lines) — pure three-way merge plus a
transactional adapter. `merge_delayed_cleanup(original, proposal,
current)` diffs original-to-proposal into edits, then admits each edit
only if the destination was not reordered, a unique boundary anchor
exists, the user has not touched that span, and the anchor still resolves
to exactly one location. `DelayedCleanupTransactionAdapter.apply` makes
proposal IDs single-use and performs read, merge, re-read,
compare-and-swap.

**`macos_networkless_worker.py` / `macos_networkless_worker_process.py`**
— an experimental, unwired one-shot sandbox boundary. The parent launches
the child through `/usr/bin/sandbox-exec` with a deny-default profile; the
child proves loopback bind and outbound connect are denied before serving,
accepts only a non-transcript `capture_proposal`, and returns a
content-free cancellation. Not imported by the runtime.

**`eval_cleanup.py`** (221 lines) — an offline harness. It imports the
live prompt constants straight from `dictate.py` so it always tests what
ships, replays adversarial cases plus recent real transcripts against
candidate Ollama models, and mirrors the production guard. Its key output
is the LEAK count: bad model output the guard did not catch.

## dictate.py anatomy

**Hotkey.** `HOTKEY = keyboard.Key.alt_r` (:489). `on_press`/`on_release`
run inside the macOS event-tap callback and only enqueue events; all real
work happens in `hotkey_worker` (:10617). A watchdog thread every 3.0 s
replaces a dead pynput listener.

**Modes.** `mode_from_modifiers` maps modifiers held at press: cmd+ctrl
command, shift+ctrl code, cmd edit, ctrl reply, shift compose, none
capture.

**Audio capture.** Three layers: `FlightRecorder` (opt-in 20 s RAM-only
ring buffer), `AudioPool` (two pre-warmed sounddevice streams so the start
cue means capture-ready), `Recorder` (one hold). `CapturedAudio` stores
samples in fixed 8-second blocks. CoreAudio listeners invalidate streams
when the default input device changes.

**LEVELS.** `LEVELS = deque([0.0]*NUM_BARS, maxlen=16)` written from the
PortAudio callback as `min(1.0, (rms*14.0)**0.5)` — a sqrt curve so
whispers register — and read by the HUD at 30 fps. `hud_level_step`
applies a 0.35 lerp and freezes at 0 under Reduce Motion.

**ASR cascade.** Three nested layers. `_speculative_frames`: decode with
Whisper Tiny first; if the Parakeet route is off and Tiny's confidence is
at least `FAST_ACCEPT_CONFIDENCE` (0.70), accept Tiny; otherwise escalate
and mark verified, retaining Tiny's disagreement as an alternative. On
macOS with the Parakeet helper present, Tiny is never accepted as final
text — it exists only for early HUD feedback. `transcribe_detailed`: on
macOS try the native helper first and return at
`PARAKEET_ROUTE_CONFIDENCE = 0.84`; otherwise run mlx-whisper /
faster-whisper. If the primary confidence is below `LOW_CONFIDENCE`
(0.52) and audio is long enough, run one independent decode at
temperature 0.4; the higher-confidence transcript wins. Whisper
large-v3-turbo degrades to Tiny (with a printed notice) when the turbo
snapshot is not installed.

**Rolling recognition.** Inside `Recorder._callback`: speculation starts
when voiced audio is at least 0.8 s with 0.25 s of silence and no future
in flight. A hard chunk cut requires a 4.0 s segment and 0.6 s silence;
pending speculation is reused when valid. Each chunk becomes a
`BoundedRecognitionFuture` carrying exact start/end samples, which
`assemble_raw` uses to keep word timings anchored across silence gaps.

**HUD.** A borderless non-activating NSPanel at status-window level,
mouse-transparent, on all Spaces, 30 fps timer. `WaveView` draws the
sticker card, radial bars, and the selected character; `_caption_add` is
the only path publishing rolling text and it publishes
`compiled.stable_prefix`, never provisional text into an app. `StatusBar`
drives the menu-bar face's mouth from the live level.

**Tones.** Per-app tone table plus menu override plus spoken override
(`TONE_OVERRIDE_RE` requires punctuation after the tone word).
`strip_casual_period` enforces the no-trailing-period chat convention.

**Snippets.** Whole-utterance snippets paste directly. Inline snippets
are masked to private-use-area sentinels before cleanup and restored
after, so multiline boilerplate is never reflowed and the model never
sees the expansion. Only capture/code modes expand inline.

**Learned corrections.** `learn_from_corrections` runs on a daemon thread
after a verified paste; it observes only the exact pasted range for 10 s,
accepts only word-shaped respellings (similarity 0.4-1.0 exclusive,
alnum, 2-30 chars), and feeds dictionary counts, deterministic fixes
(active at 3 global observations), and per-app confusions gated through
`PersonalRegressionLab.propose`, which quarantines failing candidates.

**Insertion.** `capture_insertion_lease` at key press; readable AX fields
get a full lease, opaque/terminal targets get an opaque lease sealed at
release only if input-event counters are unchanged.
`resolve_insertion_target` retries transient AX read gaps for 0.12 s but
returns immediately on real focus drift. `commit_insertion` delegates to
the InsertionCoordinator; readback timeouts are 0.02 s native and 0.35 s
for Electron apps (Chromium publishes AX values late). Anything not
verified stays in the bounded 20-item Voice Outbox.

**Transcript logging.** One JSON line per utterance in
`transcripts.jsonl` (0600), with per-stage seconds, engine, confidence,
word/prosody/decision/anchor counts, consequence route, context-firewall
aggregates, proof-edit counts, insertion state, and delayed-cleanup flag.

## Key constants

| Constant | Value | Note |
|---|---|---|
| HOTKEY | Right Option / Right Alt | |
| SAMPLE_RATE | 16000 | |
| MIN_SECONDS | 0.4 | shorter utterances dropped |
| TAIL_SECONDS | 0.30 | post-release mic tail |
| GATE_PEAK_RMS | 0.002 | just above noise floor; whispers pass |
| LOW_CONFIDENCE | 0.52 | triggers independent second decode |
| FAST_ACCEPT_CONFIDENCE | 0.70 | Tiny accept threshold (non-Parakeet) |
| PARAKEET_ROUTE_CONFIDENCE | 0.84 | routing prior, not calibrated |
| VOICE_OUTBOX_MAX_ITEMS | 20 | |
| LLM_CLEANUP_TIMEOUT | (1, 4) s | must fall back, never block paste |
| CHUNK_MIN_SECONDS / CHUNK_CUT_SILENCE | 4.0 / 0.6 | rolling cut rule |
| CORRECTION_DELAY | 10 s | learning observation window |
| PHONE_PORT | 8787 | compatibility endpoint |
| GLOSSARY_MAX_TERMS / _CHARS | 60 / 700 | Whisper prompt budget |
| PERSONAL_APP_MIN_COUNT / GLOBAL | 2 / 3 | prior activation thresholds |
| KEEPWARM_INTERVAL | 240 s | model keep-warm |

## The verifier stack and consequence cascade

Risk classification scans the primary hypothesis for URL, path, contact,
recipient, currency, date, time, bare number, spoken number, capitalized
name, and imperative action/command verbs; every category is high
severity. A risk becomes a re-listen candidate only if severity is high
AND uncertainty is non-empty AND it has native word timings. Spans are
padded 0.08 s, rejected over 2.4 s or at 75%+ of the utterance, merged
when overlapping, prioritized payload-first, and capped at 2.
`execute_consequence_plan` enforces a 0.75 s budget, refuses verifiers
that do not statically declare the safe contract
(process-isolated, strict-deadline, no-audio-retention), and rejects
results whose engine matches the primary ASR engine. Routes: standard
(no risks), review (any contradiction or unverified uncertain-high),
verified (all uncertain-high confirmed), protected (risks but none
uncertain-high). The route's only runtime effects are the completion
sound and the menu label — receipts never change recognition, cleanup,
insertion, or model routing. A verifier is used only if already
prewarmed; a cold verifier kicks off a background prewarm and the
dictation proceeds without one.

## Delayed cleanup gates

1. Requested only when the LLM is needed, mode is capture, and the
   activation receipt is valid (macOS, 0600, owned, schema-valid).
2. Scheduled only after a verified insertion receipt; a generation
   counter invalidates older threads; correction learning is suppressed
   for that utterance so the two never fight over the same field.
3. The proposal must reproduce the LLM's own output through proof edits.
4. The merge rejects reordered destinations, insufficient/changed/
   ambiguous anchors, user-touched spans, and overlapping edits (all
   colliding edits refused rather than picking a winner).
5. Apply is a compare-and-swap against a re-read snapshot with identity,
   revision, and text all matched; proposal IDs are single-use.

## Windows differences

faster-whisper + ctranslate2 (CUDA float16, CPU int8 fallback), no word
timings by default so prosody formatting and re-listen never activate;
pystray tray icon with per-state PIL faces; no HUD, GUI, Voice Outbox
menu, or App Tones; minimal AppKit shims; pyperclip/win32 clipboard with
Ctrl+V; no Accessibility so context comes from window title + clipboard
and insertion takes the legacy paste path; several macOS-only features
disabled (acoustic time machine, voice objects, spoken edit commands,
selective re-listen, delayed cleanup).

## Phone/compatibility endpoint

Port 8787: GET / and /health return ok; GET /source returns the AGPL
source-offer JSON; GET /license returns concatenated notices; POST
/v1/audio/transcriptions is OpenAI-compatible. `phone_clean` reuses the
desktop pipeline minus app context; inline snippet masking forces the
deterministic no-LLM route. Binds 127.0.0.1 unless `--server-only`
(explicit trusted-LAN mode, unauthenticated). A separate 0600 Unix
socket accepts exactly one byte to mean "show the existing GUI."

## End-to-end utterance flow (ordered)

1. Key down enqueued from the event tap; worker allocates a Recorder and
   warm audio slot.
2. Frontmost bundle, input signature, and mode resolved; start cue plays
   after capture is ready.
3. Ephemeral context collected into a ContextPack; insertion lease
   captured.
4. Whisper biasing prompt built within the 60-term/700-char budget.
5. Per-callback: level to LEVELS; VAD-driven speculation and rolling
   chunk cuts; each chunk decodes off the RT thread; stable prefix only
   to the HUD caption.
6. Key up: opaque lease sealed, release ticket issued, processing thread
   spawned; at most 0.3 s tail wait; exactly one remainder decode.
7. Energy gate drops too-short/too-quiet audio; chunks assemble into one
   Recognition with offset-corrected word timings; release order waits.
8. Hallucinations dropped, decode loops collapsed, prompt echoes dropped.
9. VoiceIR built and compiled; consequence evidence and context-firewall
   shadow comparison run.
10. Early intercepts in order: risky-action confirmation, command mode,
    voice objects, spoken edit commands, whole-utterance snippet.
11. Inline snippet masking, tone override extraction, verbatim reversion.
12. Deterministic cleanup plan; LLM only when needed, guarded and
    circuit-broken; proof validation accepts edits only if they
    reconstruct the model's own text, else deterministic cleanup pastes.
13. Casual period strip, vocabulary casing, continuation join, sentinel
    restore.
14. Insertion target resolved; one paste attempt through the coordinator
    + readback; unverified results go to the Voice Outbox.
15. Optional microspan audio retention strictly after insertion is
    terminal; delayed cleanup scheduled or correction learning thread
    started; timing line, warm-path trace, transcript append; mic
    stopped, HUD dismissal scheduled, ticket released.

## Notable invariants

1. At-most-once paste: the coordinator marks the entry terminal before
   invoking platform code; duplicate stages raise; completed utterances
   leave tombstones; mid-flight exceptions leave entries unresolved and
   recoverable, never retried.
2. Nothing provisional is ever typed into another application; only the
   stable prefix reaches the HUD caption.
3. Capture/code cleanup applies only proof-validated bounded edits; a
   failed proof discards the whole LLM result.
4. Protected anchors can never invent words; they only protect terms the
   recognizer already produced.
5. Consequence receipts are evidence-only.
6. Verifiers fail closed: prewarmed-only, safe-contract-only,
   independent-engine-only, absolute deadline, late responses discarded.
7. Only bounded spans + expected strings cross the verifier boundary in;
   only outcome/confidence/engine come out; child output goes to devnull.
8. Release order is preserved even though ASR overlaps.
9. Delayed cleanup runs only after verified insertion and user edits
   always win.
10. Correction learning is strictly downstream of a verified receipt.
11. A hold can never paste twice from the Flight buffer.
12. Private state is written atomically at 0600.
13. Single instance via flock; exit 0 stops launchd respawn loops.

## Documentation-vs-code gaps

- SpanGraph is a CONTEXT.md / ADR-0001 concept with no class; its
  implementation is `VoiceCompiler._fuse` and the literal
  `Decision(source="span-graph", ...)`.
- `macos_networkless_worker*.py` is not imported by the runtime, which
  `docs/architecture-and-interop.md` states explicitly.
