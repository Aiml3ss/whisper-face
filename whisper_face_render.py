"""Shared living-face machinery for every native Whisper Face surface.

The character *spec* lives in ``whisper_face_characters``; this module owns
how a character behaves over time and how the op list reaches Core Graphics.
The top half is deliberately AppKit-free so the blink, breath, gaze, and
mouth schedules can be tested headlessly (Windows CI included).  The bottom
half — the Core Graphics replay and the window's ``LiveFaceView`` — loads
only where AppKit exists.

Everything here is frame-counted and deterministic: schedules jitter off the
frame a cycle started on (the ``* 7919`` prime trick the HUD blink has always
used), never off wall-clock or randomness, so tests can replay any moment.
Reduce Motion callers pass ``active=False`` and every schedule returns its
resting value.
"""

from __future__ import annotations

import math

# One shared clock for every live surface.
ANIMATION_FPS = 30.0

# Blink: a six-frame lid drop, then a jittered 2.8-5.0s gap.
BLINK_FRAMES = (0.55, 1.0, 1.0, 0.6, 0.3, 0.1)
BLINK_MIN_GAP = 84           # frames between blinks (2.8s at 30fps)...
BLINK_GAP_JITTER = 66        # ...plus a deterministic 0-2.2s stagger

# Mouth envelope: open fast on a syllable, settle noticeably slower.
MOUTH_ATTACK = 0.7
MOUTH_RELEASE = 0.3

# Breathing: a slow sub-percent sine. The HUD keeps its historical subtle
# amplitude; the window face is bigger and breathes a touch deeper.
BREATH_RATE = 1.8            # radians per second
BREATH_AMP_HUD = 0.006
BREATH_AMP_WINDOW = 0.012

# Gaze: an idle face glances somewhere small every 5-9 seconds and glides
# there, so the eyes wander instead of teleporting.
SACCADE_MIN_GAP = 150        # frames (5.0s at 30fps)...
SACCADE_GAP_JITTER = 120     # ...plus a deterministic 0-4.0s stagger
GAZE_TARGETS = (
    (0.0, 0.0), (2.6, -0.8), (-2.4, -1.2), (1.8, 1.4),
    (0.0, 0.0), (-2.8, 0.6), (2.2, 0.8), (-1.6, -2.0),
)
GAZE_GLIDE = 0.16            # per-frame lerp toward the current target

# Micro-wobble: a rare spring shrug, roughly every 12-20 seconds.
WOBBLE_MIN_GAP = 360         # frames (12s at 30fps)...
WOBBLE_GAP_JITTER = 240      # ...plus a deterministic 0-8.0s stagger


def mood_overrides(mode: str) -> dict:
    """Fixed-input overrides that give non-recording phases a face.

    ``processing`` concentrates behind happy closed lids; ``error`` looks
    down, a little sheepish.  Callers apply these after the schedules so a
    mood always wins over idle drift.
    """
    if mode == "processing":
        return {"blink": 0.85, "gaze": (0.0, 0.0)}
    if mode == "error":
        return {"blink": 0.3, "gaze": (0.0, 2.5)}
    return {}


class IdleLifeDriver:
    """Deterministic frame-counted schedules for one living face."""

    def __init__(self) -> None:
        self.frame_n = 0
        self.blink_started = -42     # first blink lands 1.4-3.6s in
        self.mouth_open = 0.0
        self.gaze_x = 0.0
        self.gaze_y = 0.0
        self._gaze_target = (0.0, 0.0)
        self._gaze_pick = 0
        self._gaze_next = SACCADE_MIN_GAP
        self._wobble_next = WOBBLE_MIN_GAP

    def advance(self) -> None:
        self.frame_n += 1

    # -- mouth ----------------------------------------------------------

    def mouth(self, level: float, speaking: bool) -> float:
        """Advance the mouth envelope one frame; returns openness 0..1.

        Loudness maps through a soft knee (x^0.7) so quiet speech still
        moves the lips, the jaw opens faster than it settles, and two
        incommensurate sines add syllable texture instead of a metronome
        wobble.
        """
        if speaking:
            loud = min(1.0, max(0.0, level) ** 0.7 * 1.35)
            flutter = loud * (0.10 * math.sin(self.frame_n * 0.55)
                              + 0.06 * math.sin(self.frame_n * 0.23 + 1.7))
            target = min(1.0, max(0.0, loud + flutter))
        else:
            target = 0.0
        rate = MOUTH_ATTACK if target > self.mouth_open else MOUTH_RELEASE
        self.mouth_open = max(
            0.0, self.mouth_open + (target - self.mouth_open) * rate)
        return min(1.0, self.mouth_open)

    # -- blink ----------------------------------------------------------

    def blink(self, active: bool) -> float:
        """Occasional lid drop; resting-open when ``active`` is False.

        The gap between blinks is jittered off the frame the last blink
        started on, so the rhythm never locks to the flutter sines while
        staying deterministic for tests.
        """
        if not active:
            return 0.0
        since = self.frame_n - self.blink_started
        if since < 0:
            return 0.0
        if since < len(BLINK_FRAMES):
            return BLINK_FRAMES[since]
        gap = BLINK_MIN_GAP + (self.blink_started * 7919) % BLINK_GAP_JITTER
        if since >= gap:
            self.blink_started = self.frame_n
            return BLINK_FRAMES[0]
        return 0.0

    # -- breath ---------------------------------------------------------

    def breath(self, active: bool) -> float:
        """Raw breathing phase, -1..1; callers scale by their amplitude."""
        if not active:
            return 0.0
        return math.sin(self.frame_n / ANIMATION_FPS * BREATH_RATE)

    # -- gaze -----------------------------------------------------------

    def gaze(self, active: bool) -> tuple[float, float]:
        """Glide toward a small deterministic glance target; drift home
        when inactive so a mood change never snaps the pupils."""
        if active and self.frame_n >= self._gaze_next:
            self._gaze_pick += 1
            self._gaze_next = (self.frame_n + SACCADE_MIN_GAP +
                               (self.frame_n * 7919) % SACCADE_GAP_JITTER)
            self._gaze_target = GAZE_TARGETS[
                (self._gaze_pick * 7919) % len(GAZE_TARGETS)]
        target = self._gaze_target if active else (0.0, 0.0)
        self.gaze_x += (target[0] - self.gaze_x) * GAZE_GLIDE
        self.gaze_y += (target[1] - self.gaze_y) * GAZE_GLIDE
        return (self.gaze_x, self.gaze_y)

    # -- micro-wobble ---------------------------------------------------

    def should_wobble(self, active: bool) -> bool:
        """True exactly once per rare wobble window while active."""
        if not active:
            return False
        if self.frame_n >= self._wobble_next:
            self._wobble_next = (self.frame_n + WOBBLE_MIN_GAP +
                                 (self.frame_n * 7919) % WOBBLE_GAP_JITTER)
            return True
        return False


try:  # pragma: no cover - exercised only where AppKit exists
    import objc  # noqa: F401
    from AppKit import (
        NSAffineTransform,
        NSBezierPath,
        NSColor,
        NSGraphicsContext,
        NSMakeRect,
        NSTimer,
        NSView,
    )

    RENDER_APPKIT_AVAILABLE = True
except Exception:  # pragma: no cover
    RENDER_APPKIT_AVAILABLE = False
    NSView = object

if RENDER_APPKIT_AVAILABLE:
    from whisper_face_characters import (
        Arc,
        Curve,
        Ellipse,
        Polygon,
        RoundedRect,
        Stroke,
        character_ops,
    )
    from whisper_face_theme import jelly_face_scale

    def _render_rgb(r, g, b, a=1.0):
        NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a).set()

    def _render_poly(points):
        path = NSBezierPath.bezierPath()
        path.moveToPoint_(points[0])
        for point in points[1:]:
            path.lineToPoint_(point)
        path.closePath()
        return path

    def replay_ops(ops) -> None:
        """Draw a shared character op list through Core Graphics.

        The single Core Graphics twin of ``whisper_face_characters._svg_op``;
        the HUD and the window both call this so the two renderers cannot
        drift apart.
        """
        for op in ops:
            if isinstance(op, Ellipse):
                _render_rgb(*op.color, op.alpha)
                NSBezierPath.bezierPathWithOvalInRect_(
                    NSMakeRect(op.x, op.y, op.w, op.h)).fill()
            elif isinstance(op, Polygon):
                _render_rgb(*op.color, op.alpha)
                _render_poly(op.points).fill()
            elif isinstance(op, Stroke):
                _render_rgb(*op.color, op.alpha)
                path = NSBezierPath.bezierPath()
                path.setLineWidth_(op.width)
                path.setLineCapStyle_(1)
                path.setLineJoinStyle_(1)
                path.moveToPoint_(op.points[0])
                for point in op.points[1:]:
                    path.lineToPoint_(point)
                path.stroke()
            elif isinstance(op, RoundedRect):
                _render_rgb(*op.color, op.alpha)
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    NSMakeRect(op.x, op.y, op.w, op.h),
                    op.radius, min(op.radius, op.h / 2.0)).fill()
            elif isinstance(op, Arc):
                _render_rgb(*op.color, op.alpha)
                path = NSBezierPath.bezierPath()
                path.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_(
                    (op.cx, op.cy), op.radius,
                    op.start_degrees, op.end_degrees)
                path.setLineWidth_(op.width)
                path.setLineCapStyle_(1)
                path.stroke()
            elif isinstance(op, Curve):
                _render_rgb(*op.color, op.alpha)
                path = NSBezierPath.bezierPath()
                path.moveToPoint_(op.start)
                for control_one, control_two, end in op.segments:
                    path.curveToPoint_controlPoint1_controlPoint2_(
                        end, control_one, control_two)
                path.closePath()
                path.fill()

    class LiveFaceView(NSView):
        """The window's living character: breath, blinks, glances, speech.

        The view never starts its own clock at construction — the smoke
        tests and the render probe build windows with no runloop — and it
        draws a correct static frame whenever the timer is off.  Visibility
        callers use ``startLiving``/``stopLiving``; feeding a level via
        ``setLevel_mode_`` is how recording reaches the mouth.
        """

        def initWithFrame_(self, frame):
            self = objc.super(LiveFaceView, self).initWithFrame_(frame)
            if self is None:
                return None
            self.face = "parrot"
            self.mode = "idle"
            self.raw = 0.0
            self.lv = 0.0
            self.reduce_motion = False
            self.life = IdleLifeDriver()
            self._timer = None
            self._wobble_hook = None
            return self

        def isFlipped(self):
            return True

        # -- state ------------------------------------------------------

        def setFace_(self, face):
            if face != self.face:
                self.face = face
                self.setNeedsDisplay_(True)

        def setLevel_mode_(self, level, mode):
            self.raw = max(0.0, min(1.0, float(level)))
            if mode != self.mode:
                self.mode = mode
                self.setNeedsDisplay_(True)

        def setReduceMotion_(self, flag):
            flag = bool(flag)
            if flag != self.reduce_motion:
                self.reduce_motion = flag
                if flag:
                    self.stopLiving()
                self.setNeedsDisplay_(True)

        def setWobbleHook_(self, hook):
            """Install the controller's spring callback for rare shrugs."""
            self._wobble_hook = hook

        # -- clock ------------------------------------------------------

        def startLiving(self):
            if self._timer is not None or self.reduce_motion:
                return
            self._timer = \
                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    1.0 / ANIMATION_FPS, self, "tick:", None, True)

        def stopLiving(self):
            if self._timer is not None:
                self._timer.invalidate()
                self._timer = None

        def isLiving(self):
            return self._timer is not None

        def tick_(self, _timer):
            self.life.advance()
            smoothing = 0.35
            target = self.raw if self.mode == "recording" else 0.0
            self.lv = self.lv + (target - self.lv) * smoothing
            if (self._wobble_hook is not None
                    and self.mode == "idle"
                    and self.life.should_wobble(not self.reduce_motion)):
                try:
                    self._wobble_hook()
                except Exception:
                    pass
            self.setNeedsDisplay_(True)

        # -- drawing ----------------------------------------------------

        def drawRect_(self, _rect):
            bounds = self.bounds().size
            side = min(bounds.width, bounds.height)
            if side <= 0:
                return
            animate = not self.reduce_motion and self._timer is not None
            lv = max(0.0, min(1.0, self.lv))

            mouth = self.life.mouth(
                lv, animate and self.mode == "recording")
            blink = self.life.blink(animate)
            gaze = self.life.gaze(animate and self.mode == "idle")
            mood = mood_overrides(self.mode)
            if "blink" in mood:
                blink = max(blink, mood["blink"])
            if "gaze" in mood and self.mode != "idle":
                gaze = mood["gaze"]

            scale_x, scale_y = jelly_face_scale(
                lv,
                processing=self.mode == "processing",
                reduce_motion=not animate,
            )
            breath = BREATH_AMP_WINDOW * self.life.breath(animate)
            scale_x *= 1.0 - breath * 0.5
            scale_y *= 1.0 + breath

            ctx = NSGraphicsContext.currentContext()
            ctx.saveGraphicsState()
            transform = NSAffineTransform.transform()
            transform.translateXBy_yBy_(
                (bounds.width - side) / 2.0, (bounds.height - side) / 2.0)
            transform.scaleBy_(side / 256.0)
            transform.translateXBy_yBy_(128.0, 128.0)
            transform.scaleXBy_yBy_(scale_x, scale_y)
            transform.translateXBy_yBy_(-128.0, -128.0)
            transform.concat()
            replay_ops(character_ops(
                self.face, mouth, lv, blink, gaze=gaze))
            ctx.restoreGraphicsState()
