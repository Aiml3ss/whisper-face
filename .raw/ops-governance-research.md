# Research brief: operations, distribution, site, and governance

Codebase research over the Whisper Face repository (2026-07-26), covering
installers, services, updates, packaging, the native helper, the
marketing site, the benchmark family, tests, and governance. References
are against commit `b49699f`.

## Installers

Entry points: `Install.command` (Mac) and `Install.cmd` (Windows) are
thin shims over `setup.sh` / `setup.ps1`. `setup.sh` is also the platform
router: MinGW/MSYS/Cygwin/WSL re-exec `setup.ps1`; bare Linux is an
explicit hard failure. Flags: `--server-only` and `--verify` on both;
`--with-all-models` and `--models` on Mac only. A default Mac install is
deliberately small (Parakeet Unified + Whisper Tiny, ~650 MB); Whisper
large-v3-turbo and Qwen3.5-4B are opt-in (~5 GB more). Windows always
installs Tiny + Turbo + Qwen.

Mac flow: required-file manifest (~140 entries), arm64 gate, free-space
gate, writable-checkout proof (fails on read-only DMG before any
download), private 0600 log provisioning before services can create
them, Homebrew + uv/ffmpeg/ollama, model inventory probe so
announcements reflect actual work, Parakeet Core ML preload (pinned
revision) + Swift helper build + verify, Ollama service with a warm fast
path (byte-identical plist + digest receipt + launchd PID equals unique
listener PID skips reload), locked Python env from `dictate.py.lock`,
private per-machine state from templates only when absent, dictation
LaunchAgent, launcher app install, readiness wait tailing the log for
Ready or a permissions prompt, then health check and full verify.

Windows flow: strict mode, manifest, Win32NT + x64 gates, winget
bootstrap with PATH refresh, private ACL-locked logs, Ollama started
only if unreachable, qwen pull, locked sync, model preloads, private
files, a generated 3-line launcher shim whose SHA-256 is receipted, task
registration (current user, limited run level, restart on failure), then
a 180 s health poll and verification.

Verify modes are read-only. Mac verifies tools, helper, plists (lint +
byte-match + digest receipt + PID identity), logs, launcher binding,
locks, a bounded native GUI smoke test, model pins, agents, and health.
Windows verifies tools, logs, task-launcher binding (digest, principal,
single action, exact argument string), locks, models, and health. The
documented asymmetry: Mac proves launchd-PID-to-listener identity;
Windows cannot because Ollama is independently managed there.

Idempotency invariant: generated services are replaced; private user
files survive (templates copy only when absent; permissions are repaired
without truncating); package/model steps skip when satisfied;
verification never skips.

## Services

Two Mac LaunchAgents: `com.berg.dictate` (RunAtLoad, umask 077,
KeepAlive except on successful exit so the single-instance lock's exit 0
stops respawn, Interactive process type) and `com.berg.ollama`
(KeepAlive, OLLAMA_FLASH_ATTENTION=1, OLLAMA_NUM_PARALLEL=1). Plist
rendering fails if any placeholder survives, lints, chmods 600, and
installs atomically; reload is bootout, poll, bootstrap, kickstart.

The launcher app (`scripts/macos_launcher_app.py`, bundle
com.berg.whisper-face.launcher) exists so macOS attributes Input
Monitoring / Accessibility / Microphone to one grantable "Whisper Face"
identity that survives reinstalls. Swift source is embedded in the
Python script; the bundle contains only Info.plist, the executable, a
source hash, and the icon — never the runtime or checkout paths. Machine
binding lives outside the bundle in a 0600 receipt. Local installs get
a deterministic ad-hoc signature; a non-ad-hoc bundle must satisfy the
pinned Developer ID requirement from `config/macos-signing-policy.json`
(currently null team id, so signing is disabled until the owner records
it). At runtime the launcher validates the receipt and asks launchd to
start the existing service, then writes exactly one byte to a 0600 Unix
socket whose name binds PID + source revision; the service shows its
existing GUI.

Health endpoint on 8787: `/` and `/health` return ok; `/source` returns
the AGPL source-offer JSON with the exact revision; `/license` returns
concatenated notices; the transcription POST is OpenAI-compatible.
Unauthenticated by design and documented as a trust boundary; loopback
unless an explicit `--server-only` install.

## Update and rollback

Two deliberately separate paths. Path A, shipping today: menu-driven
self-update (`self_update.py`) — exactly one network operation
(git fetch), fail-closed on dirty trees or unresolvable upstreams, apply
records the previous HEAD, runs the installer, and rolls back to the
previous revision on failure; a detached launchd job variant exists
because the installer reloads the service that would otherwise kill the
updater; results persist to a 0600 state file consumed on restart;
errors are privacy-scrubbed. No background polling.

Path B, release-operator tooling: `safe_update_advisor.py` (read-only
verdicts up-to-date/upgrade/rollback/refuse; verifies manifests against
local artifact hashes; stable channels additionally require live
codesign + stapler validation) and `side_by_side_update.py` (validates a
separately prepared clean sibling checkout; only `--apply` executes the
candidate's own setup.sh and verify; the current checkout is never
altered and remains the rollback copy).

`release_manifest.py` is stdlib-only (auditable before dependencies
exist): create/verify/source-metadata/checksums, strict SemVer + 40-hex
+ https validators, rollback linkage, and an installation contract
(source-bundle-in-place, same-checkout reinstall preserves private
state, no automatic cross-checkout migration).

`package_macos.sh` builds from one exact revision: git archive,
re-init shallow metadata so /source can prove the revision, optional
sign+notarize+staple (validating the Apple team against the packaged
revision's own signing policy), deterministic tree stamping
(`verify_macos_package.py`) because DMGs are not byte-reproducible, ZIP
+ DMG + manifest + SHA256SUMS.

## Native helper

`native/ParrotASRHelper`: Swift 6, macOS 14+, single pinned dependency
FluidAudio 0.15.5, 92-line main. Modes: preload/verify emit a JSON ok
line; server loops reading an 8-byte little-endian sample count (capped
at 10 minutes), then Float32 PCM, and emits JSON text/latency lines.
Audio is never written to disk. The runtime keeps one persistent helper
process behind a lock, frames requests with adaptive deadlines and a
64 KB response cap, and on any failure closes the process, falls back to
Whisper Turbo faithfully, and lazily restarts next call. Windows never
sees this path.

## Marketing site

Astro 5 + Tailwind 4 + sitemap, fully static, site whisperface.com.
Content collections for docs (getting started, permissions, privacy,
troubleshooting) and blog (two posts). Home composition: Nav, Hero,
Marquee, Features, HowItWorks, FacesGallery, PrivacyBand, Install, Faq,
Footer. The ten characters ship as generated inline SVG (idle/half/talk
frames). Jelly UI is loaded from its CDN with an SRI pin recorded in
THIRD_PARTY_NOTICES. Deploy target is Cloudflare Pages (project
whisper-face) via the Git integration building `site/` — deliberately no
GitHub Actions site workflow. Immutable caching for hashed assets plus
nosniff/referrer/frame headers. Cloudflare Web Analytics is wired but
off. DEPLOY.md keeps an honest pre-launch TODO list.

## The benchmark family

Shared philosophy: no runtime authority. Every lab is offline, opt-in,
and transcript-free; reports are aggregate-only; synthetic evidence
never masquerades as physical validation; unavailable is a first-class
result. The development ledger's canonical demonstration: two proposed
model changes and a writev framing change were all rejected on lab
evidence and nothing in the runtime moved.

Root-level labs: ASR bakeoff (LibriSpeech, research audio outside the
repo), macOS warm-path profile, voice-compiler golden corpus, cleanup
latency (opt-in, local Ollama only), cleanup proof recovery, consequence
routing, insertion reliability (simulation-only, explicitly no four-nines
claim), re-listen activation, acoustic calibration + activation, keyword
activation + bias fixtures. Aggregators: `performance_lab.py` (corpus,
evaluate, traces, startup, warm-path, lifecycle, stress, scorecard,
audit-models; fixed schema identifiers so user strings never become
aggregate keys; budget profiles; insufficient-samples instead of false
regressions), `public_scorecard.py` (aggregates checked-in synthetic
suites), `competitor_benchmark.py` (neutral task protocol over
externally collected observations; never runs products or ranks;
measured vs unavailable vs claimed-only).

Corpora live in `benchmarks/` with case counts and privacy stamps; the
shipping ASR baseline table (2026-07-21, M4 Pro) records Parakeet
1.240% WER / 113x realtime vs Turbo 1.717% / 4.4x vs Tiny 7.010% /
120x — the stated justification for the Tiny-Parakeet-Turbo cascade.

## Tests and release gates

62 unittest-based PEP 723 test files run individually via uv. Groups:
pure decision logic, cleanup pipeline, verifiers, ASR/models, acoustics,
insertion/targeting, voice objects/inbox/drafts, protocol/isolation,
runtime+GUI (test_dictate is 6,758 lines), labs, distribution/ops
(test_installers AST-parses both installers for contract parity),
governance (pinned license hashes, MIT boundary commit, CLA
enforcement, honest-claims checks).

The release-gate list lives in AGENTS.md step 5 (~58 commands) and is
duplicated in docs/installer-release-process.md and the macOS release
workflow. Known drift: AGENTS.md includes test_whisper_face_characters
while the other two lists do not; the release workflow omits
test_acoustic_time_machine and test_acoustic_calibration_activation;
a few tests appear in no gate list (spoken edit commands runtime,
self_update, benchmark acoustic variants). Gates end with the live
platform verify command.

## Governance

License structure: AGPL-3.0-only for current first-party source; a
separate signed commercial license (the document itself grants nothing);
historical MIT through commit 8f317df7 preserved and not revoked, with
the boundary documented in LICENSE_POLICY and NOTICE. No trademark
rights to the name or character artwork. The Mac window and the network
endpoint both expose the source offer and notices.

CLA: contributors retain copyright and grant a broad relicensing +
patent license; acceptance must be an affirmative recorded statement.
The ledger (.github/cla-signatures-v1.json) pins the CLA sha256, the
owner login and immutable numeric id, and an acceptance list (currently
empty). The CI check reads the ledger from the PR's base SHA so a
contributor branch can never supply its own, and a bootstrap path is
restricted to the owner. CODEOWNERS requires owner review on all
governance files and workflows.

Security: private vulnerability reporting only, response targets stated
as targets, safe harbor, seven named high-value areas. Privacy: no
account, upload, sale, or shared-model training; RAM-only audio;
disclosed local files with the honest note that local does not mean
anonymous; port 8787 disclosed; any future network feature must be
opt-in with named operator, purpose, retention, and deletion. Support:
the free-local covenant — core accuracy/privacy/accessibility/
correction/export/deletion/recovery are never Supporter-only.

ADRs (all accepted): 0001 compile evidence instead of rewriting
transcripts; 0002 stable prefixes are committed conservatively; 0003
final insertion is an exactly-once local transaction; 0004 personal
priors must pass a private regression suite.

CI workflows (5): cla-check (required status check; runs on the
self-hosted bergserver runner since commit 8fb7ed5, because GitHub-hosted
jobs stop starting when the Actions budget is spent; python3 not python),
windows-smoke (hosted, no longer a required check), macos-release
(dispatch/tag; full gate list; pristine-worktree assertion; Apple
credentials confined to the package job; SHA-pinned actions; isolated
publish job), model-audit (weekly; uploads evidence then propagates
failure), performance-lifecycle (weekly). No site-deploy workflow by
design.
