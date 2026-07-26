/**
 * Runtime for the shared jelly motions.
 *
 * The curves themselves live in `motion.ts` and are baked into `@keyframes` at
 * build time; this is only the trigger. It exists as its own module so the
 * layout, the hero, and the faces gallery all reach for the same gate — in
 * particular the same Reduce Motion gate.
 */
import type { MotionName } from './motion';

const reduceMotion =
  typeof matchMedia === 'function' ? matchMedia('(prefers-reduced-motion: reduce)') : null;

/** True when the visitor has asked for less movement. Read live, never cached. */
export function prefersReducedMotion(): boolean {
  return reduceMotion ? reduceMotion.matches : false;
}

/**
 * Play one named motion on an element.
 *
 * Reduce Motion returns false without touching the element, so the squish is
 * absent rather than shortened. The return value makes the gate testable.
 */
export function playJelly(element: Element | null | undefined, motion: MotionName): boolean {
  if (!(element instanceof HTMLElement)) return false;
  if (prefersReducedMotion()) return false;
  // Clearing first restarts the animation when the same motion fires twice in a
  // row — a second click should squash again, not sit still.
  element.removeAttribute('data-jelly-play');
  void element.offsetWidth;
  element.setAttribute('data-jelly-play', motion);
  element.addEventListener(
    'animationend',
    () => {
      if (element.getAttribute('data-jelly-play') === motion) {
        element.removeAttribute('data-jelly-play');
      }
    },
    { once: true },
  );
  return true;
}
