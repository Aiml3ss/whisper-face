/**
 * Jelly motion, shared with the Mac app.
 *
 * `MOTION_SPECS` below is a straight mirror of `whisper_face_theme.MOTION_SPECS`.
 * The app hands those seven numbers to `CASpringAnimation` (see
 * `add_jelly_motion` in `whisper_face_gui.py`): for each axis it springs
 * `transform.scale.{x,y}` from the squash value back to `1.0` using the spec's
 * mass, stiffness, damping and initial velocity.
 *
 * This module integrates the same second-order ODE that Core Animation solves —
 * `m·x'' + c·x' + k·x = 0` — and bakes the result into `@keyframes`, so a button
 * on whisperface.com travels the exact curve the same button travels in the app.
 * Jelly UI itself exposes no physics API (its springs are baked per component),
 * so this is the only way the two surfaces can share one set of numbers.
 *
 * Keep this table and `whisper_face_theme.MOTION_SPECS` in lockstep.
 */

export type MotionName = 'press' | 'release' | 'wobble' | 'pop';

export interface MotionSpec {
  /** Mass of the body, in CASpringAnimation units. */
  mass: number;
  /** Spring stiffness (k). Higher settles faster. */
  stiffness: number;
  /** Damping coefficient (c), not a ratio. Lower wobbles longer. */
  damping: number;
  /** Speed already pointing at rest when the spring starts, in units/second. */
  initialVelocity: number;
  /** Settle time in seconds. */
  duration: number;
  /** Horizontal scale the motion starts from; it springs back to 1. */
  squashX: number;
  /** Vertical scale the motion starts from; it springs back to 1. */
  squashY: number;
}

export const MOTION_SPECS: Record<MotionName, MotionSpec> = {
  press: {
    mass: 1,
    stiffness: 420,
    damping: 28,
    initialVelocity: 0,
    duration: 0.24,
    squashX: 1.05,
    squashY: 0.94,
  },
  release: {
    mass: 1,
    stiffness: 360,
    damping: 22,
    initialVelocity: 0.5,
    duration: 0.34,
    squashX: 0.96,
    squashY: 1.05,
  },
  wobble: {
    mass: 1,
    stiffness: 330,
    damping: 16,
    initialVelocity: 0.8,
    duration: 0.46,
    squashX: 1.07,
    squashY: 0.93,
  },
  pop: {
    mass: 1,
    stiffness: 390,
    damping: 20,
    initialVelocity: 0.4,
    duration: 0.38,
    squashX: 0.9,
    squashY: 0.9,
  },
};

export const MOTION_NAMES = Object.keys(MOTION_SPECS) as MotionName[];

/**
 * Fraction of the distance still to travel at time `t`.
 *
 * Starts at 1 (fully squashed) and decays to 0 (at rest). Underdamped specs dip
 * below zero on the way — that dip is the overshoot you feel as the wobble.
 */
function remainingDistance(spec: MotionSpec, t: number): number {
  const omega0 = Math.sqrt(spec.stiffness / spec.mass);
  const zeta = spec.damping / (2 * Math.sqrt(spec.stiffness * spec.mass));
  // x(0) = 1, x'(0) = -initialVelocity: the body is already heading home.
  const v0 = -spec.initialVelocity;
  const decay = Math.exp(-zeta * omega0 * t);
  if (zeta < 1) {
    const omegaD = omega0 * Math.sqrt(1 - zeta * zeta);
    const b = (v0 + zeta * omega0) / omegaD;
    return decay * (Math.cos(omegaD * t) + b * Math.sin(omegaD * t));
  }
  if (zeta === 1) {
    return decay * (1 + (v0 + omega0) * t);
  }
  const omegaR = omega0 * Math.sqrt(zeta * zeta - 1);
  const b = (v0 + zeta * omega0) / omegaR;
  return decay * (Math.cosh(omegaR * t) + b * Math.sinh(omegaR * t));
}

/** Scale on one axis at time `t`, springing from `squash` back to 1. */
function axisScale(spec: MotionSpec, squash: number, t: number): number {
  return 1 + (squash - 1) * remainingDistance(spec, t);
}

function trim(value: number, places: number): string {
  return String(Number(value.toFixed(places)));
}

/**
 * How many samples a motion needs: one every 10ms, clamped.
 *
 * These springs oscillate at roughly 3Hz, so linear interpolation across a 10ms
 * gap is accurate to well under a thousandth of the deformation — far finer than
 * a display refresh can show.
 */
function sampleCount(spec: MotionSpec): number {
  return Math.min(40, Math.max(12, Math.round(spec.duration / 0.01)));
}

/**
 * Bake one named motion into a `@keyframes` rule.
 *
 * The animation drives the standalone `scale` property rather than `transform`,
 * so the sticker press (`transform: translate(...)`) and the tilts on face tiles
 * keep working underneath it.
 */
export function motionKeyframes(name: MotionName): string {
  const spec = MOTION_SPECS[name];
  const steps = sampleCount(spec);
  const frames: string[] = [];
  let previous = '';
  for (let i = 0; i <= steps; i += 1) {
    const t = (i / steps) * spec.duration;
    const x = i === steps ? 1 : axisScale(spec, spec.squashX, t);
    const y = i === steps ? 1 : axisScale(spec, spec.squashY, t);
    const value = `${trim(x, 4)} ${trim(y, 4)}`;
    // Linear interpolation between the frames we keep reproduces the curve, so
    // frames that round to their predecessor carry no information.
    if (value === previous && i !== steps) continue;
    previous = value;
    frames.push(`${trim((i / steps) * 100, 3)}%{scale:${value}}`);
  }
  return `@keyframes wf-${name}{${frames.join('')}}`;
}

/**
 * The full motion stylesheet: one duration custom property and one keyframe
 * rule per named motion. Injected inline by `Base.astro` so there is exactly
 * one place these numbers live.
 */
export function motionStylesheet(): string {
  const durations = MOTION_NAMES.map(
    (name) => `--wf-${name}-duration:${Math.round(MOTION_SPECS[name].duration * 1000)}ms;`,
  ).join('');
  const keyframes = MOTION_NAMES.map(motionKeyframes).join('');
  return `:root{${durations}}${keyframes}`;
}
