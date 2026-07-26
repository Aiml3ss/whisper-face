# Research brief: the 2026-07-26 interface rebuild

Codebase research over the Whisper Face repository covering the five
interface changes that shipped after the first wiki build: the menu-bar
simplification (#101), the window's information-architecture collapse
(#104), the visual rebuild around the shared design language (#105), the
first-run and states pass (#112), and the site/app motion parity work
(#111). Every claim below was re-read against the tree at commit
`1165335`; where a commit message and the code disagree, the code is
recorded and the disagreement is called out.

The wiki built earlier the same day (at `b49699f`) describes the *old*
menu and the *old* window. This brief is the delta.

## 1. The menu bar is now a quick-glance surface (#101, `9c0d6a4`)

`StatusBar.init` builds the macOS menu once at `dictate.py:2734-2773`.
The in-code rationale sits above it (:2727-2733): "The default menu is
six choices."

**Always present**, in assembly order (:2759-2772):

1. `Open Whisper Face…` → `openGUI:`
2. + 3. two non-actionable usage lines (`mk(..., None)`, :2735-2736)
   refreshed in `menuWillOpen_` from `usage_stats()` (:2566-2595):
   `Today: {n} dictations · {n} words` and `Last 7 days: {n} · {n} words`
4. `Pause Dictation` / `Resume Dictation`
5. `Choose Face` — the only submenu in the whole menu (`setSubmenu_`
   appears exactly once, :2740), rebuilt each open by `rebuild_faces()`
   (:2868-2876) over the ten `FACE_CHOICES` (:646-649), radio-checked
   against `current_face()`
6. `Check for Updates…` → `checkForUpdates:`
7. `Quit Whisper Face` → `quitApp:`

That is seven `NSMenuItem`s but six *choices*: the two usage rows are one
disabled group.

**Conditional rows**, created hidden and unhidden per open in
`menuWillOpen_` (:2827-2864):

- **Last Recognition** (:2741-2742, `openResults:`) —
  `refresh_recognition_item()` (:2939-2952) sets
  `setHidden_(not PIPELINE_STATE["last_result_evidence"])`, so the row
  appears once a first result exists. `recognition_root_title`
  (:5879-5882) returns `Last Recognition — Review` only on the `review`
  consequence route, otherwise `Last Recognition`. Enabled only when the
  GUI is available.
- **Voice Outbox** (:2746-2748) and **Voice Inbox** (:2743-2745) —
  hidden while `INSERTION_COORDINATOR.recoverable_count()` and
  `voice_object_inbox_status()["queued_count"]` are zero (:2851, :2860).
  Titles carry bounded counts (`voice_inbox_menu_title` :1611,
  `voice_outbox_menu_title` :1619-1620). Outbox sits above Inbox.
- **Selective Re-listen** (:2751-2753) — `refresh_relisten_item()`
  (:2954-2968) computes
  `available = runtime_relisten["evidence_ready"] or runtime_relisten["requested"]`.
  **Correction to the commit message's framing**: the row is *not*
  strictly receipt-only. `evidence_ready` is
  `IS_MACOS and load_activation_receipt(RELISTEN_ACTIVATION_FILE).ready`
  (:1344-1368), but the row also appears when the `selective_relisten`
  preference is on without a ready receipt — that is what makes the
  status titles (`: On` / `: Warming` / `: Starting` / `: Off`)
  meaningful. What is true is that a dormant default install with the
  preference off and no receipt shows no row at all.

**Gone from the menu entirely**: the tones submenu, the learned-
corrections row, the six-row mode cheat sheet, the Flight Recorder row,
`Open Log`, and the dense Last Recognition evidence submenu. Fourteen
`addItem_` calls remain (:2759-2772 plus the faces loop at :2876). The
macOS `openLog:` item was deleted in `9c0d6a4`; `grep openLog` now finds
only the Windows `_open_log`.

**Nothing was lost, only moved.** Tones, snippets, vocabulary, learned
corrections, pronunciation keywords, and the mode reference live under
Settings → Personalize; Flight Recorder under Settings → Privacy;
alternatives and evidence in the Home evidence inspector; `Open Log`
under Advanced. All reachable through the menu's first row.

**Two cleanups**: `builtin_tone` died with the tones submenu and has zero
matches repo-wide; `tone_for` (:5907) keeps its menu-override-then-per-
app-sets resolution unchanged. The Flight Recorder `NSMenuItem` object
survives *unattached* (:2757, never `addItem_`'d, with a comment at
:2754-2756) because `refresh_flight_item`, `start_flight_async`,
`toggleFlight_`, `set_flight_enabled`, and `set_paused` still drive its
state, and `set_flight_enabled` is what the window's Privacy toggle calls
(`dictate.py:10370`).

**Windows is untouched.** The pystray tray (:3311-3323) still shows five
entries: Choose Face, Flight Recorder (RAM only), Pause Dictation, Open
Log, Quit. No usage lines, no window opener, no update check, no recovery
rows, no re-listen. `9c0d6a4` touched no pystray line.

## 2. The window collapsed to Home / Settings / Advanced (#104, `2dede55`)

`whisper_face_gui.py:32-33`:

```python
SECTIONS = ("Home", "Settings", "Advanced")
SETTINGS_PANES = ("Personalize", "Privacy")
```

Five sections (Overview, Results, Settings, Models, Diagnostics) became
three; three Settings panes (Modes, Personalize, Privacy) became two. The
nav catalog keys are `nav.home` / `nav.settings` / `nav.advanced`
(:177-179); internal `overview.*` / `results.*` / `models.*` /
`diagnostics.*` content keys deliberately kept their old names to
minimise render churn.

**Home** (`_build_home`, :5518-5817) — the old Overview plus the Results
summary surface:

- hero card (156pt): phase pill (READY / RECORDING / PROCESSING /
  RECOVERY AVAILABLE / ACTION NEEDED / PAUSED / STARTING LOCALLY), status
  title and detail, engine line, a two-line-wrapping outbox line, and
  Pause / Review Setup / Copy & Dismiss;
- metrics card (80pt): Last dictation, Words today, Time saved;
- **Last dictation** card (136pt, :5612-5666): summary, mode pill, engine
  and audio lines, and three symbol buttons — Play Span, Clear, Inspect
  Evidence (same selectors and opt-in gating as the old Results page);
- the onboarding overlay poster, which hides the result card while first
  run is unfinished (:6418).

**Settings** — a two-segment control (:5837-6020):

- *Personalize*: the face-picker card (identity, not privacy) above six
  44pt rows — App tones, Snippets, Vocabulary, Learned corrections,
  Pronunciation keywords, and a new **Voice modes** row whose View dialog
  lists the six Right Option shortcuts (:7292-7302, catalog :463-478),
  replacing the deleted zero-interactive Modes tab.
- *Privacy*: exactly three switch rows — Voice Object Commands (with an
  Inspect button), Flight Recorder, Acoustic Time Machine.

**Advanced** (`_build_advanced`, :6021-6212) — Models and Diagnostics
merged: the Selective Re-listen row above four model rows with readiness
pills, the wallet shadow advisory and model guidance, a 2×3 status card
(Service, Microphone, Accessibility, Personal Regression Lab, Motion,
Build), then Open Log, Copy Support Snapshot, Run Verification (⌘R,
spinner preserved), Export Support Bundle…, Open System Settings,
License Notices, Exact Source, the verification label and the license
footnote.

**The trust surface moved without shrinking.** The persistent evidence
and assurance cards are gone as chrome; `result_evidence_text` gained a
keyword-only `result` parameter (:2529-2533) and, when it is supplied,
emits a `RESULT SUMMARY` section first (:2554-2593): stable prefix words,
protected-anchor count, compiler decisions with confidence appended,
alternatives considered, deduplicated cleanup kinds, proof accepted /
rejected, context influence, context-firewall summary, consequence
summary, and the consequence review advisory when non-empty — followed by
the existing ALTERNATIVES / PROTECTED ANCHORS / PROOF EDITS / TIMING
sections. `inspectResultEvidence_` (:7879-7901) passes
`result=self.view_model.state.last_result`; the smoke test asserts the
whole surface with the comment "The persistent evidence/assurance cards
are gone; the explicit evidence reveal must carry the entire trust
surface instead." `show_results()` opens Home and, when a result exists,
that modal.

**Four experimental surfaces left the window — and only the window.**
Demonstrations authoring, the risky-action confirmation ceremony, the
Point-and-Speak preview/press dialogs, and the Drop-to-Target preview
dialogs have no selector, button, dialog method, or catalog row on any
page. What remains, verified at HEAD:

- `GUIActions` fields: `inspect_/create_/reveal_/record_/approve_/
  cancel_/delete_approved_demonstration_draft` (:959-972),
  `start_/click_/cancel_risky_action_confirmation` (:973-976),
  `preview_point_and_speak` / `issue_point_and_speak_nonce` /
  `press_point_and_speak` (:997-1001), `preview_drop_to_target` (:1002),
  all still listed in the contract's action names (:850-865);
- view-model passthroughs for every one of them (risky :3432/3448/3461;
  demonstrations :3706-3931; point-and-speak :4090/4105/4128;
  drop-to-target :4138);
- the runtime modules `demonstration_drafts.py`,
  `risky_action_confirmation.py`, `point_and_speak_resolver.py`,
  `point_and_speak_transaction.py`, `macos_point_and_speak_snapshot.py`,
  `drop_to_target.py`, `macos_drop_to_target_snapshot.py`, each with its
  own test file.

**Correction to the commit message.** It says the four remain "separately
tested (tests/test_gui_settings_runtime.py passes unchanged)". That file
covers only the *risky-action* runtime (`RiskyConfirmationRuntimeIntegration
Tests`, :26-158); it contains zero references to demonstrations,
Point-and-Speak, or Drop-to-Target. Those three view-model layers are
tested in `tests/test_whisper_face_gui.py` instead (point-and-speak
:167/:186/:1192/:1216/:1237, drop-to-target :265/:1273/:1304,
demonstrations :2043/:2095, risky :1691/:1712). The substantive claim —
they are developer-invokable and still covered — holds; the file
attribution does not.

**Keyboard contract** (:915-919): `return:continue-setup`,
`command-d:advanced`, `command-r:verification`. ⌘D is attached to the
always-visible Advanced *sidebar row* (:5370-5379), not to a control on
the Advanced page, so it works from every section; #104 put it on the
page and #105 moved it to the rail.

## 3. The window was rebuilt around the design language (#105 `e7b41ee`, #112 `7359baa`)

**Skeleton.** A 200pt `NSVisualEffectView` sidebar
(`NSVisualEffectMaterialSidebar`, behind-window blending, :5285-5291,
`SIDEBAR_WIDTH = 200.0` :4347) replaces the segmented section control.
Rows are 36pt tall on a 40pt pitch with SF Symbols `house.fill`,
`gearshape.fill`, `wrench.and.screwdriver.fill` (:5332-5336). The header
carries a 48pt rotated face chip and the `LOCAL FIRST` badge
(`app.local_badge`, :186); the foot carries `BUILD {version}`
(`app.version`, :187). The window is resizable, opens at 1000×640 and
will not go below 880×600 (`setContentMinSize_((880.0, 600.0))`,
:5271-5279). Content lives in a centred column capped at
`CONTENT_WIDTH = 720.0` (:4348) whose preferred-width constraint is
deliberately priority 450 — below `NSLayoutPriorityWindowSizeStayPut`
(500) — so long trust copy can never inflate the window (:5411-5413).

**Controls.** `JellySwitch(NSSwitch)` (:4566) replaces checkbox buttons;
the smoke gate asserts all four toggles are `NSSwitch` (:7664-7669) and
the file contains no `setButtonType` call at all.

**Type and rhythm.** 22pt semibold rounded page titles (`_page_header`
:5261), 17pt rounded headings (:5316/5533/5620), 13pt body and 11pt
captions (`_row` defaults :5807-5808); the old 8/9/10pt strays are gone.
44pt control rows throughout Settings, 36pt dense rows in the Advanced
model list (:6030/6060).

*Two honest caveats about the "8pt grid" description.* The column's 24pt
inset is its **top** only — horizontally the column is centred with a
12pt minimum leading, not inset (:5431-5441). Card gaps are 16pt on Home
and Personalize but 12 / 6 / 2 on Advanced (:6085-6207), and the only
8pt-grid assertion in the code is poster-local and already carves out a
10pt optical exception (:4908). The scale also has a fifth step the
summary omits: the onboarding poster title is 30pt bold rounded (:5725).

**Colour and appearance.** The app never calls `setAppearance_` — it
reads the system's via
`effectiveAppearance().bestMatchFromAppearancesWithNames_` (:4587-4594)
and resolves palettes through `whisper_face_theme.palette_for_appearance`
(13 call sites). `_apply_window_theme` (:5071-5128) repaints the root,
cards, hairlines, ink roles, CTA fills, chip, title, badge, version, and
every rail row on each render. Because the raw emerald and amber fail
WCAG AA as text on light surfaces, text-bearing uses swap in darkened
inks (:4332-4336): `BRAND_TEXT_ON_LIGHT ≈ #0B7A57` (5.3:1 on white) and
`AMBER_TEXT_ON_LIGHT ≈ #7A4F00` (6.1:1). Dark mode gets more than a
palette swap: different rail alphas (:5147-5153), pill fills at 0.30 vs
0.16 (:5820), and a neutral ink wash on the current onboarding chip
instead of amber-over-pine, which used to read olive (:5006).

**Motion.** `add_jelly_motion` (:4446) looks the motion up in
`whisper_face_theme.MOTION_SPECS` and attaches two `CASpringAnimation`s
on `transform.scale.x` / `.y`, keyed `whisper-face-{motion}-{axis}`
(:4466-4477). The specs (`whisper_face_theme.py:105-110`, fields
`mass, stiffness, damping, initial_velocity, duration, squash_x,
squash_y`):

| motion | mass | stiff | damp | v0 | dur | sqx | sqy |
|---------|------|-------|------|-----|------|------|------|
| press | 1.0 | 420 | 28 | 0.0 | 0.24 | 1.05 | 0.94 |
| release | 1.0 | 360 | 22 | 0.5 | 0.34 | 0.96 | 1.05 |
| wobble | 1.0 | 330 | 16 | 0.8 | 0.46 | 1.07 | 0.93 |
| pop | 1.0 | 390 | 20 | 0.4 | 0.38 | 0.90 | 0.90 |

Reduce Motion is the function's first line: `if reduced_motion or view is
None: return False` (:4455-4456), and it reports `False` "so callers can
verify the gate". The flag comes from
`NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion()`
(:4746-4748 in the GUI, `dictate.py:2676-2685` for the HUD), is mirrored
into the module global `_REDUCE_MOTION` on every `render()`
(:6292-6296), and every jelly control reads it at press time. *Honest
gap*: there is no
`NSWorkspaceAccessibilityDisplayOptionsDidChangeNotification` observer
anywhere — the value is polled through `runtime_status_snapshot()`
(`dictate.py:4836`, `:4935`) on the refresh timer, so a mid-session
toggle takes effect at the next refresh rather than immediately.

**The anchor-point bug.** AppKit hands view-backed layers an anchor of
`(0, 0)`, so every scale spring was growing out of the bottom-left
corner and the squash read as a slide. `center_layer_anchor`
(:4418-4444) re-anchors to `(0.5, 0.5)` and shifts the position by
exactly the distance the anchor moved:

```python
layer.setAnchorPoint_((0.5, 0.5))
layer.setPosition_((
    position.x + (0.5 - anchor.x) * size.width,
    position.y + (0.5 - anchor.y) * size.height,
))
```

so layer frames are unchanged. A byte-identical twin fixes the HUD and
menu-bar port at `dictate.py:2093-2117` (`_center_layer_anchor`, called
from `_add_jelly_animation` :2120-2124, which drives the HUD pop :2540
and the menu-bar mouth wobble :2830). The window fix is asserted by the
smoke gate (:7682-7685).

**The amber CTA contrast bug.** `_set_cta_title` (:4823-4838) sets an
attributed title with SF Rounded 13pt semibold and
`_theme_color(LIGHT_PALETTE.ink)` (`#0E2A24`) on the amber
(`#FBBF24`) fill. At the previous revision `e7b41ee`, `LIGHT_PALETTE`
appeared exactly once in the file — the *use* — and was missing from the
import block, so a `NameError` was raised inside the `try` and swallowed,
`setAttributedTitle_` never ran, and every amber CTA kept AppKit's
default title colour. `7359baa` added the import. *Two corrections to the
commit message*: the swallow was `except Exception: pass`, not a bare
`except`; and neither the literal `controlTextColor` nor the "about
1.9:1" figure exists anywhere in the code — both are the commit's
description of AppKit's default behaviour, not measurements the tree can
reproduce.

**Chrome motion and hover.** Section switches crossfade at
`CROSSFADE_SECONDS = 0.15` with `kCAMediaTimingFunctionEaseOut`
(:4483-4512, `_animate_section_change` :5180-5194, which also pops the
newly selected rail row); a fresh notice fades in (:5196-5215). Both are
Reduce-Motion gated and both are window chrome only — the comment at
:4480-4482 says so explicitly ("never touch the dictation hot path").
Sidebar rows hover through an `NSTrackingArea` with
`NSTrackingInVisibleRect` (:4538-4549) so scroll and resize stay correct
without rebuilding the region, and `_set_hovered` early-returns on
no-change so only the row whose state flipped repaints (:4552-4554).

**First run in the product's voice** (#112). Four steps plus an explicit
completion branch (`onboarding_presentation` :2286-2345). Titles
(:254-260, :284): "First, let me hear you", "Now try your key", "Getting
your models ready", "Say something", and "Nice. You’re ready." (with a
typographic apostrophe). The poster is the only sticker-offset surface
(`SURFACE_SPECS["playful"]`): a 208pt circular chip tilted 3° holding a
144pt face (:5679-5690), a real 6pt progress bar under the eyebrow
(:5709-5721, comment: "A real bar makes '2 of 4' something you feel
instead of read"), a kicker reading "Everything you say stays on this
Mac." (:288), and four 52pt step chips (:5749-5772) — filled brand check
for done, amber numbered disc for current, quiet outline for ahead.
`windowDidResize_` re-flows the whole composition (:7453-7461 →
`_layout_onboarding` :4885) so it holds at the 880pt minimum. The
character opens its mouth on "Say something" and completion, wobbles once
per new step, and each chip pops when its step turns complete; nothing
loops and Reduce Motion silences all of it. Decorative poster elements
leave the VoiceOver tree; the progress bar carries the completion count.

**Every state in one voice.** The 39 `operation.*` (:667-705) and 21
`validation.*` (:644-664) messages all name what failed, what is still
true, and the one move that unsticks it — e.g. "Could not update Flight
Recorder: {error}. It stayed exactly as it was — try the switch again."
Trust surfaces stay exact: "Confirmation stayed blocked: this click was
not preceded by a valid voice receipt. Say the confirmation phrase again,
then click." Empty strings became invitations: "Your first dictation
lands here" / "Say a sentence and I’ll show what I heard, protected, and
delivered." (:332-333), "Teach me the names I keep getting wrong"
(:488), "Fix the same word a few times and I’ll remember it" (:491),
"Dictate somewhere and that app shows up here" (:482), "Voice Outbox: all
clear" (:212). Personalization rows show the invitation instead of a "0"
until there is something to count. Longer sentences forced truncation
guards: the notice band takes a second line (16pt → 28pt, :4361-4362)
and the Advanced advisory and guidance blocks wrap.

**The render probe.** `scripts/window_render_probe.py` (198 lines, PEP
723, `uv run`) is committed and renders headlessly: it builds the
controller through `initForSmokeWithViewModel_` — the no-system-state
path — never orders the window front, and captures via
`cacheDisplayInRect_toBitmapImageRep_`. It writes nine views
(`window-home`, `window-settings-personalize`,
`window-settings-privacy`, `window-advanced`, and the five first-run
stages) in both `NSAppearanceNameAqua` and `NSAppearanceNameDarkAqua` —
18 PNGs — into the gitignored `.probe-renders/`. `--size WIDTHxHEIGHT`
lets the 880×600 minimum be reviewed directly.

## 4. The site and the app now run the same springs (#111, `9c54f09`)

**What the library actually is.** Reading `jelly.js` changed the shape of
the answer: every `jelly-*` element paints its soft body into a canvas
inside its shadow root filled with a flat `--jelly-fill`, and it exposes
**no physics API** — its springs are baked per component. Its buttons are
`<button>` elements while every call to action on the site is an
`<a href>`. Adopting `jelly-*` literally would have traded the site's
sticker identity (3px `--line` border, zero-blur offset shadow, chunky
radii) for Jelly's look, turned download links into buttons, and still
left the two surfaces running different numbers — and the Mac app cannot
use the library at all, because it translates `MOTION_SPECS` into
`CASpringAnimation`. Parity therefore means both surfaces running the
same named motions with the same constants. The no-physics-API finding is
recorded in the tree at `THIRD_PARTY_NOTICES.md:84` and
`site/src/data/motion.ts:13-14`; the canvas/`--jelly-fill` detail at
`site/src/styles/global.css:258-261`. (The survey's finer details — the
count of the library's custom properties, its `part="jelly"` /
`part="button"` exports — live only in the `9c54f09` commit message and
appear in no file.)

**The mirror.** `site/src/data/motion.ts` declares exactly
`'press' | 'release' | 'wobble' | 'pop'` (:19) and carries all 28
constants identical to `whisper_face_theme.MOTION_SPECS`, in the same
field order. `remainingDistance` (:85-102) solves
`m·x'' + c·x' + k·x = 0` across all three damping regimes (underdamped
cos/sin, critical, overdamped cosh/sinh) — the same second-order ODE Core
Animation integrates — and `motionKeyframes` (:131-148) bakes the
solution into `@keyframes wf-{name}` at one frame per 10 ms clamped to
12-40 frames, with `--wf-{name}-duration` custom properties alongside
(:155-161). Re-integrating the ODE independently reproduces the app's
curve: press 1.05/0.94 → 0.9974/1.0031 → 1 over 240 ms, release
0.96/1.05 → 1.0043/0.9947 → 1 over 340 ms, wobble ζ≈0.44 ringing through
two decaying lobes over 460 ms. (The commit message's "pop travels 0.90 →
1.0139 → 1" is slightly off: the baked frames peak at ~1.0157 near
t≈190 ms. A browser sample taken next to the peak, not the baked value.)

**Why `scale`, not `transform`.** The keyframes animate the standalone
`scale` property (:145), documented at :124-130 and
`global.css:1301-1307`, so the sticker push (`transform: translate(...)`)
and the face-tile tilts keep working underneath. `will-change: scale`
(:1314).

**The trigger.** `site/src/data/jelly.ts` (43 lines) holds only the
runtime: `playJelly(element, motion)` bails on a non-element, then
`if (prefersReducedMotion()) return false;` (:27) — off, not shortened —
clears `data-jelly-play`, forces a reflow so a repeat motion restarts,
sets the attribute, and removes it on `animationend`. Reduce Motion is
gated in three layers: `playJelly` returns false without touching the
element, `global.css:1342-1348` zeroes `animation` and sets
`scale: none !important` on `[data-jelly]`, and the pre-paint script in
`site/src/layouts/Base.astro:58-69` stamps
`data-jelly-motion="reduce" | "no-preference"` on `<html>` and re-applies
it on change — explicitly "rather than left to jelly.js's own fallback so
the guarantee is auditable in the served HTML", which also gates the
library's own canvas physics.

**Hooks.** Press fires on `pointerdown`, release on
`pointerup`/`pointercancel`, plus Enter/Space (`Base.astro:108-119`).
`9c54f09` newly hooked the nav links, the brand, the footer links, the
docs sidebar and the hero face; the install CTA, 404 buttons, gallery
cards, hero CTAs and switcher swatches already carried `data-jelly` and
got a new *runtime* rather than a new hook. A user-picked face pops and
its swatch wobbles (`Hero.astro:96-101`) — `renderPicked()` runs only
from the switcher click and the gallery `wf:setface` event, never from
the idle carousel, which calls plain `render()`; the pop lands on the
face rather than the chip because the chip owns the permanent `bob`
float.

**The theme toggle** stays a real `jelly-button` (`Nav.astro:18-25`)
running the library's own baked spring — it carries no `data-jelly` — but
wears the site's clothes (3px sticker frame and hard shadow on the host,
`--jelly-fill: var(--teal)` on the candy body, `global.css:262-276`) and
degrades through a `:not(:defined)` rule (:287-294) if the CDN ever
fails. The pinned SRI hash
(`sha384-ftCoNMap6OQSid+PyZ/rndaaw9grzCxUOYiXbTN1fxw1OYuI95Gm/HwyjOknBIG5`,
`Base.astro:47`) was recomputed against the live 318,426-byte bundle and
is unchanged since it was introduced; `THIRD_PARTY_NOTICES.md` now
records that verification and the split between library components and
first-party springs.

**Two 404 fixes found on the way.** `.face-frames` is rendered by
`Face.astro`, so it carries that component's scope id — the page-scoped
rule never matched and the owl had collapsed to nothing; the fix is
`.notfound-face :global(.face-frames)` (`404.astro:65`). And an inline
`padding: 90px 0 100px` shorthand was zeroing `.wrap`'s 24px gutter, so
long copy ran into both edges on a phone; the fix moves to a scoped class
using `padding-block` (:35). Copy now says what happened and where to go
next, in the app's empty-state voice.

## 5. Documentation that the code contradicts

Recorded here rather than fixed, because this brief only describes state:

- `README.md:437` — "The **Last Recognition** submenu ends with **Open
  Last Result…**". There is no submenu (Last Recognition is a single item
  with the `openResults:` action) and no "Open Last Result…" row anywhere
  in the repo.
- `README.md:497` — "the **always-available** Voice Inbox menu-bar
  entry". The row is hidden whenever the queue is empty.
- `site/DEPLOY.md:52` — "Point the Download / Get it on GitHub buttons at
  the real signed, notarized release once it exists (they currently go to
  the repo, and the install section says 'building from source today')".
  The download button has pointed at a real release DMG since `c079a9d`,
  and the install copy no longer says that. Only the notarization half is
  still true.
