// The sixteen Whisper Face faces, as inline SVG.
// Each face has an `idle` and a `talk` frame (mouth open + little sound bars).
// "MASK" is a placeholder swapped for a unique id per rendered instance so
// multiple faces on one page never collide on the same <mask> id.

import { FACE_ART, FACE_VIEWBOX } from './face-art';

// Warm character-outline ink from the chibi-clay art, so the flat marks sit
// in the same clay world as the colored faces on their pastel chips.
const INK = '#33281f';

export type Animal =
  | 'fox' | 'bear' | 'owl' | 'parrot' | 'cat'
  | 'dog' | 'wolf' | 'pig' | 'panda' | 'tiger'
  | 'frog' | 'rabbit' | 'hedgehog' | 'penguin'
  | 'pickles' | 'olive';

export interface FaceDef {
  color: string; // CSS custom property for the chip behind the face
  label: string;
  role: string; // playful one-liner
  line: string; // sample dictation the hero types out
  idle: string;
  talk: string;
}

export const FACES: Record<Animal, FaceDef> = {
  fox: {
    color: 'var(--fox)',
    label: 'Fox',
    role: 'sly & quick',
    line: 'quick note — grab coffee, catch the 4pm train, text back later',
    idle: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="24" cy="31" r="2.8"/><circle cx="40" cy="31" r="2.8"/><ellipse cx="32" cy="45" rx="5" ry="1.8"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M8 26 C4 12 10 1.5 14.5 3 C19.5 4.6 27 12.5 30.5 18.5 C24.5 23.5 15.5 26 8 26 Z"/><path d="M56 26 C60 12 54 1.5 49.5 3 C44.5 4.6 37 12.5 33.5 18.5 C39.5 23.5 48.5 26 56 26 Z"/><circle cx="32" cy="35" r="25"/><path d="M17 48 C20.5 57.5 25.5 62 32 62 C38.5 62 43.5 57.5 47 48 C37 51.5 27 51.5 17 48 Z"/></g>`,
    talk: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="24" cy="30" r="2.8"/><circle cx="40" cy="30" r="2.8"/><ellipse cx="32" cy="46" rx="6" ry="6"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M8 26 C4 12 10 1.5 14.5 3 C19.5 4.6 27 12.5 30.5 18.5 C24.5 23.5 15.5 26 8 26 Z"/><path d="M56 26 C60 12 54 1.5 49.5 3 C44.5 4.6 37 12.5 33.5 18.5 C39.5 23.5 48.5 26 56 26 Z"/><circle cx="32" cy="35" r="25"/><path d="M17 48 C20.5 57.5 25.5 62 32 62 C38.5 62 43.5 57.5 47 48 C37 51.5 27 51.5 17 48 Z"/></g><rect fill="${INK}" x="48" y="8" width="11" height="5" rx="2.5"/><rect fill="${INK}" x="52" y="17" width="10" height="5" rx="2.5"/>`,
  },
  bear: {
    color: 'var(--bear)',
    label: 'Bear',
    role: 'calm & steady',
    line: 'dear team, the new build ships tonight. no typos, i promise.',
    idle: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="23" cy="31" r="2.5"/><circle cx="41" cy="31" r="2.5"/><ellipse cx="32" cy="46" rx="4" ry="1.7"/></mask><g fill="${INK}" mask="url(#MASK)"><circle cx="14" cy="14" r="11"/><circle cx="50" cy="14" r="11"/><circle cx="32" cy="36" r="26"/></g>`,
    talk: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="23" cy="30" r="2.5"/><circle cx="41" cy="30" r="2.5"/><ellipse cx="32" cy="47" rx="5" ry="6"/></mask><g fill="${INK}" mask="url(#MASK)"><circle cx="14" cy="14" r="11"/><circle cx="50" cy="14" r="11"/><circle cx="32" cy="36" r="26"/></g><rect fill="${INK}" x="48" y="8" width="11" height="5" rx="2.5"/><rect fill="${INK}" x="52" y="17" width="10" height="5" rx="2.5"/>`,
  },
  owl: {
    color: 'var(--owl)',
    label: 'Owl',
    role: 'wide awake',
    line: 'def clean(text): return text.strip()  # yep, it does code too',
    idle: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="23" cy="32" r="7"/><circle cx="41" cy="32" r="7"/><path d="M28 45 Q32 41.8 36 45 Q32 47.6 28 45 Z"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M6.5 26 C4 13 9.5 1.8 15 3.2 C20.5 4.6 27.5 12.5 31 18.5 C24 23.5 13.5 26 6.5 26 Z"/><path d="M57.5 26 C60 13 54.5 1.8 49 3.2 C43.5 4.6 36.5 12.5 33 18.5 C40 23.5 50.5 26 57.5 26 Z"/><circle cx="32" cy="36" r="26"/><path d="M15 49 H49 C46.5 57.5 40.5 62 32 62 C23.5 62 17.5 57.5 15 49 Z"/></g>`,
    talk: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="23" cy="31" r="7"/><circle cx="41" cy="31" r="7"/><path d="M25 44 Q32 38.6 39 44 Q32 53.4 25 44 Z"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M6.5 26 C4 13 9.5 1.8 15 3.2 C20.5 4.6 27.5 12.5 31 18.5 C24 23.5 13.5 26 6.5 26 Z"/><path d="M57.5 26 C60 13 54.5 1.8 49 3.2 C43.5 4.6 36.5 12.5 33 18.5 C40 23.5 50.5 26 57.5 26 Z"/><circle cx="32" cy="36" r="26"/><path d="M15 49 H49 C46.5 57.5 40.5 62 32 62 C23.5 62 17.5 57.5 15 49 Z"/></g><rect fill="${INK}" x="48" y="8" width="11" height="5" rx="2.5"/><rect fill="${INK}" x="52" y="17" width="10" height="5" rx="2.5"/>`,
  },
  parrot: {
    color: 'var(--parrot)',
    label: 'Parrot',
    role: 'the original',
    line: 'hold a key, say the thing, and it just shows up at your cursor',
    idle: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="24" cy="31" r="2.8"/><circle cx="40" cy="31" r="2.8"/><path d="M27 40 H37 C36.4 44.6 34.6 47.8 32 49 C29.4 47.8 27.6 44.6 27 40 Z"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M32 2.5 C35 4 36.8 10 36.2 16.5 C33.5 18 30.5 18 27.8 16.5 C27.2 10 29 4 32 2.5 Z"/><path d="M20.5 4.5 C24.5 5.5 28 10.5 29 15.8 C26 17 21.5 16.2 18.3 14.4 C18.2 10.5 19 6.5 20.5 4.5 Z"/><path d="M43.5 4.5 C39.5 5.5 36 10.5 35 15.8 C38 17 42.5 16.2 45.7 14.4 C45.8 10.5 45 6.5 43.5 4.5 Z"/><circle cx="32" cy="37" r="24"/></g>`,
    talk: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="24" cy="30" r="2.8"/><circle cx="40" cy="30" r="2.8"/><path d="M25 39 H39 C38.2 45 35.8 50 32 52 C28.2 50 25.8 45 25 39 Z"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M32 2.5 C35 4 36.8 10 36.2 16.5 C33.5 18 30.5 18 27.8 16.5 C27.2 10 29 4 32 2.5 Z"/><path d="M20.5 4.5 C24.5 5.5 28 10.5 29 15.8 C26 17 21.5 16.2 18.3 14.4 C18.2 10.5 19 6.5 20.5 4.5 Z"/><path d="M43.5 4.5 C39.5 5.5 36 10.5 35 15.8 C38 17 42.5 16.2 45.7 14.4 C45.8 10.5 45 6.5 43.5 4.5 Z"/><circle cx="32" cy="37" r="24"/></g><rect fill="${INK}" x="48" y="8" width="11" height="5" rx="2.5"/><rect fill="${INK}" x="52" y="17" width="10" height="5" rx="2.5"/>`,
  },
  cat: {
    color: 'var(--cat)',
    label: 'Cat',
    role: 'quietly judging',
    line: 'buy oat milk. cancel the gym. book the flight. sigh.',
    idle: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="23" cy="31" r="2.5"/><circle cx="41" cy="31" r="2.5"/><ellipse cx="32" cy="46" rx="4" ry="1.7"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M7.5 25 C4.5 11.5 9.5 1.5 14 3 C19 4.7 27 13 30.5 18.5 C24 23 15 25 7.5 25 Z"/><path d="M56.5 25 C59.5 11.5 54.5 1.5 50 3 C45 4.7 37 13 33.5 18.5 C40 23 49 25 56.5 25 Z"/><circle cx="32" cy="36" r="25"/></g><g fill="none" stroke="${INK}" stroke-width="3" stroke-linecap="round"><path d="M3 44.5H14"/><path d="M50 44.5H61"/><path d="M3 55 14 51.8"/><path d="M61 55 50 51.8"/></g>`,
    talk: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="23" cy="30" r="2.5"/><circle cx="41" cy="30" r="2.5"/><ellipse cx="32" cy="47" rx="5" ry="6"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M7.5 25 C4.5 11.5 9.5 1.5 14 3 C19 4.7 27 13 30.5 18.5 C24 23 15 25 7.5 25 Z"/><path d="M56.5 25 C59.5 11.5 54.5 1.5 50 3 C45 4.7 37 13 33.5 18.5 C40 23 49 25 56.5 25 Z"/><circle cx="32" cy="36" r="25"/></g><g fill="none" stroke="${INK}" stroke-width="3" stroke-linecap="round"><path d="M3 43.5H14"/><path d="M50 43.5H61"/><path d="M3 54 14 50.8"/><path d="M61 54 50 50.8"/></g><rect fill="${INK}" x="48" y="6" width="11" height="5" rx="2.5"/>`,
  },
  dog: {
    color: 'var(--dog)',
    label: 'Dog',
    role: 'eager to please',
    line: 'good news — walk at five, treats after, belly rubs on demand',
    idle: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="24" cy="31" r="2.8"/><circle cx="40" cy="31" r="2.8"/><ellipse cx="32" cy="45" rx="5" ry="1.8"/></mask><g fill="${INK}" mask="url(#MASK)"><ellipse cx="11" cy="33" rx="7" ry="13"/><ellipse cx="53" cy="33" rx="7" ry="13"/><circle cx="32" cy="35" r="23"/></g>`,
    talk: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="24" cy="30" r="2.8"/><circle cx="40" cy="30" r="2.8"/><ellipse cx="32" cy="46" rx="6" ry="6"/></mask><g fill="${INK}" mask="url(#MASK)"><ellipse cx="11" cy="33" rx="7" ry="13"/><ellipse cx="53" cy="33" rx="7" ry="13"/><circle cx="32" cy="35" r="23"/></g><rect fill="${INK}" x="48" y="8" width="11" height="5" rx="2.5"/><rect fill="${INK}" x="52" y="17" width="10" height="5" rx="2.5"/>`,
  },
  wolf: {
    color: 'var(--wolf)',
    label: 'Wolf',
    role: 'runs with the pack',
    line: 'howl if you need me. otherwise i am on the ridge, watching the moon.',
    idle: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="24" cy="32" r="2.6"/><circle cx="40" cy="32" r="2.6"/><ellipse cx="32" cy="46" rx="4" ry="1.7"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M10 25 C7.5 13 12 0.8 16.5 2.2 C21 3.6 27.5 13.5 30 19.5 C24 23.5 16 25 10 25 Z"/><path d="M54 25 C56.5 13 52 0.8 47.5 2.2 C43 3.6 36.5 13.5 34 19.5 C40 23.5 48 25 54 25 Z"/><path d="M10.5 42 C6 45.5 2.5 49.5 1.8 52.2 C5.5 52.3 10.8 51 15 48.8 C14 46 12.5 43.5 10.5 42 Z"/><path d="M53.5 42 C58 45.5 61.5 49.5 62.2 52.2 C58.5 52.3 53.2 51 49 48.8 C50 46 51.5 43.5 53.5 42 Z"/><circle cx="32" cy="36" r="23"/></g>`,
    talk: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="24" cy="31" r="2.6"/><circle cx="40" cy="31" r="2.6"/><ellipse cx="32" cy="47" rx="5" ry="6"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M10 25 C7.5 13 12 0.8 16.5 2.2 C21 3.6 27.5 13.5 30 19.5 C24 23.5 16 25 10 25 Z"/><path d="M54 25 C56.5 13 52 0.8 47.5 2.2 C43 3.6 36.5 13.5 34 19.5 C40 23.5 48 25 54 25 Z"/><path d="M10.5 42 C6 45.5 2.5 49.5 1.8 52.2 C5.5 52.3 10.8 51 15 48.8 C14 46 12.5 43.5 10.5 42 Z"/><path d="M53.5 42 C58 45.5 61.5 49.5 62.2 52.2 C58.5 52.3 53.2 51 49 48.8 C50 46 51.5 43.5 53.5 42 Z"/><circle cx="32" cy="36" r="23"/></g><rect fill="${INK}" x="48" y="8" width="11" height="5" rx="2.5"/><rect fill="${INK}" x="52" y="17" width="10" height="5" rx="2.5"/>`,
  },
  pig: {
    color: 'var(--pig)',
    label: 'Pig',
    role: 'happy as mud',
    line: 'grocery run: truffle oil, fresh mud, and absolutely no bacon.',
    idle: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="24" cy="30" r="2.6"/><circle cx="40" cy="30" r="2.6"/><circle cx="28" cy="45" r="2"/><circle cx="36" cy="45" r="2"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M15.5 16 C18 9.5 22.5 5.2 26.5 5 C29 8.5 30.3 14.5 29.8 20.3 C24.5 21.3 19 19.5 15.5 16 Z"/><path d="M48.5 16 C46 9.5 41.5 5.2 37.5 5 C35 8.5 33.7 14.5 34.2 20.3 C39.5 21.3 45 19.5 48.5 16 Z"/><circle cx="32" cy="36" r="24"/></g>`,
    talk: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="24" cy="29" r="2.6"/><circle cx="40" cy="29" r="2.6"/><circle cx="28" cy="44" r="2"/><circle cx="36" cy="44" r="2"/><ellipse cx="32" cy="51" rx="5" ry="3.5"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M15.5 16 C18 9.5 22.5 5.2 26.5 5 C29 8.5 30.3 14.5 29.8 20.3 C24.5 21.3 19 19.5 15.5 16 Z"/><path d="M48.5 16 C46 9.5 41.5 5.2 37.5 5 C35 8.5 33.7 14.5 34.2 20.3 C39.5 21.3 45 19.5 48.5 16 Z"/><circle cx="32" cy="36" r="24"/></g><rect fill="${INK}" x="48" y="8" width="11" height="5" rx="2.5"/><rect fill="${INK}" x="52" y="17" width="10" height="5" rx="2.5"/>`,
  },
  panda: {
    color: 'var(--panda)',
    label: 'Panda',
    role: 'snack then nap',
    line: 'meeting moved to noon. i will be the one eating bamboo in the back.',
    idle: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><ellipse cx="23" cy="33" rx="7" ry="9"/><ellipse cx="41" cy="33" rx="7" ry="9"/><circle cx="23" cy="34" r="2.5" fill="#fff"/><circle cx="41" cy="34" r="2.5" fill="#fff"/><ellipse cx="32" cy="48" rx="4" ry="1.7"/></mask><g fill="${INK}" mask="url(#MASK)"><circle cx="14" cy="14" r="10"/><circle cx="50" cy="14" r="10"/><circle cx="32" cy="36" r="25"/></g>`,
    talk: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><ellipse cx="23" cy="32" rx="7" ry="9"/><ellipse cx="41" cy="32" rx="7" ry="9"/><circle cx="23" cy="33" r="2.5" fill="#fff"/><circle cx="41" cy="33" r="2.5" fill="#fff"/><ellipse cx="32" cy="48" rx="5" ry="6"/></mask><g fill="${INK}" mask="url(#MASK)"><circle cx="14" cy="14" r="10"/><circle cx="50" cy="14" r="10"/><circle cx="32" cy="36" r="25"/></g><rect fill="${INK}" x="48" y="8" width="11" height="5" rx="2.5"/><rect fill="${INK}" x="52" y="17" width="10" height="5" rx="2.5"/>`,
  },
  tiger: {
    color: 'var(--tiger)',
    label: 'Tiger',
    role: 'quietly fierce',
    line: 'email marketing: the subject line needs more roar, less whisker.',
    idle: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="23" cy="31" r="2.6"/><circle cx="41" cy="31" r="2.6"/><ellipse cx="32" cy="45" rx="4" ry="1.7"/><rect x="30.9" y="11" width="2.2" height="9" rx="1.1"/><rect x="24.4" y="13" width="2.2" height="8" rx="1.1"/><rect x="37.4" y="13" width="2.2" height="8" rx="1.1"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M7.5 25 C4.5 11.5 9.5 1.5 14 3 C19 4.7 27 13 30.5 18.5 C24 23 15 25 7.5 25 Z"/><path d="M56.5 25 C59.5 11.5 54.5 1.5 50 3 C45 4.7 37 13 33.5 18.5 C40 23 49 25 56.5 25 Z"/><circle cx="32" cy="36" r="25"/></g>`,
    talk: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="23" cy="30" r="2.6"/><circle cx="41" cy="30" r="2.6"/><ellipse cx="32" cy="47" rx="5" ry="6"/><rect x="30.9" y="11" width="2.2" height="9" rx="1.1"/><rect x="24.4" y="13" width="2.2" height="8" rx="1.1"/><rect x="37.4" y="13" width="2.2" height="8" rx="1.1"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M7.5 25 C4.5 11.5 9.5 1.5 14 3 C19 4.7 27 13 30.5 18.5 C24 23 15 25 7.5 25 Z"/><path d="M56.5 25 C59.5 11.5 54.5 1.5 50 3 C45 4.7 37 13 33.5 18.5 C40 23 49 25 56.5 25 Z"/><circle cx="32" cy="36" r="25"/></g><rect fill="${INK}" x="48" y="8" width="11" height="5" rx="2.5"/><rect fill="${INK}" x="52" y="17" width="10" height="5" rx="2.5"/>`,
  },
  frog: {
    color: 'var(--frog)',
    label: 'Frog',
    role: 'leaps at it',
    line: 'todo: water the plants, answer greg, and leap on that invoice',
    idle: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="23" cy="31" r="2.6"/><circle cx="41" cy="31" r="2.6"/><ellipse cx="32" cy="45" rx="4" ry="1.7"/><rect x="30.9" y="11" width="2.2" height="9" rx="1.1"/><rect x="24.4" y="13" width="2.2" height="8" rx="1.1"/><rect x="37.4" y="13" width="2.2" height="8" rx="1.1"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M7.5 25 C4.5 11.5 9.5 1.5 14 3 C19 4.7 27 13 30.5 18.5 C24 23 15 25 7.5 25 Z"/><path d="M56.5 25 C59.5 11.5 54.5 1.5 50 3 C45 4.7 37 13 33.5 18.5 C40 23 49 25 56.5 25 Z"/><circle cx="32" cy="36" r="25"/></g>`,
    talk: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="23" cy="30" r="2.6"/><circle cx="41" cy="30" r="2.6"/><ellipse cx="32" cy="47" rx="5" ry="6"/><rect x="30.9" y="11" width="2.2" height="9" rx="1.1"/><rect x="24.4" y="13" width="2.2" height="8" rx="1.1"/><rect x="37.4" y="13" width="2.2" height="8" rx="1.1"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M7.5 25 C4.5 11.5 9.5 1.5 14 3 C19 4.7 27 13 30.5 18.5 C24 23 15 25 7.5 25 Z"/><path d="M56.5 25 C59.5 11.5 54.5 1.5 50 3 C45 4.7 37 13 33.5 18.5 C40 23 49 25 56.5 25 Z"/><circle cx="32" cy="36" r="25"/></g><rect fill="${INK}" x="48" y="8" width="11" height="5" rx="2.5"/><rect fill="${INK}" x="52" y="17" width="10" height="5" rx="2.5"/>`,
  },
  rabbit: {
    color: 'var(--rabbit)',
    label: 'Rabbit',
    role: 'all ears',
    line: 'shopping list: carrots, more carrots, one very fast pair of shoes',
    idle: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="23" cy="31" r="2.6"/><circle cx="41" cy="31" r="2.6"/><ellipse cx="32" cy="45" rx="4" ry="1.7"/><rect x="30.9" y="11" width="2.2" height="9" rx="1.1"/><rect x="24.4" y="13" width="2.2" height="8" rx="1.1"/><rect x="37.4" y="13" width="2.2" height="8" rx="1.1"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M7.5 25 C4.5 11.5 9.5 1.5 14 3 C19 4.7 27 13 30.5 18.5 C24 23 15 25 7.5 25 Z"/><path d="M56.5 25 C59.5 11.5 54.5 1.5 50 3 C45 4.7 37 13 33.5 18.5 C40 23 49 25 56.5 25 Z"/><circle cx="32" cy="36" r="25"/></g>`,
    talk: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="23" cy="30" r="2.6"/><circle cx="41" cy="30" r="2.6"/><ellipse cx="32" cy="47" rx="5" ry="6"/><rect x="30.9" y="11" width="2.2" height="9" rx="1.1"/><rect x="24.4" y="13" width="2.2" height="8" rx="1.1"/><rect x="37.4" y="13" width="2.2" height="8" rx="1.1"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M7.5 25 C4.5 11.5 9.5 1.5 14 3 C19 4.7 27 13 30.5 18.5 C24 23 15 25 7.5 25 Z"/><path d="M56.5 25 C59.5 11.5 54.5 1.5 50 3 C45 4.7 37 13 33.5 18.5 C40 23 49 25 56.5 25 Z"/><circle cx="32" cy="36" r="25"/></g><rect fill="${INK}" x="48" y="8" width="11" height="5" rx="2.5"/><rect fill="${INK}" x="52" y="17" width="10" height="5" rx="2.5"/>`,
  },
  hedgehog: {
    color: 'var(--hedgehog)',
    label: 'Hedgehog',
    role: 'spiky but sweet',
    line: 'note to self: softer in the standup, sharper in the doc',
    idle: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="23" cy="31" r="2.6"/><circle cx="41" cy="31" r="2.6"/><ellipse cx="32" cy="45" rx="4" ry="1.7"/><rect x="30.9" y="11" width="2.2" height="9" rx="1.1"/><rect x="24.4" y="13" width="2.2" height="8" rx="1.1"/><rect x="37.4" y="13" width="2.2" height="8" rx="1.1"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M7.5 25 C4.5 11.5 9.5 1.5 14 3 C19 4.7 27 13 30.5 18.5 C24 23 15 25 7.5 25 Z"/><path d="M56.5 25 C59.5 11.5 54.5 1.5 50 3 C45 4.7 37 13 33.5 18.5 C40 23 49 25 56.5 25 Z"/><circle cx="32" cy="36" r="25"/></g>`,
    talk: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="23" cy="30" r="2.6"/><circle cx="41" cy="30" r="2.6"/><ellipse cx="32" cy="47" rx="5" ry="6"/><rect x="30.9" y="11" width="2.2" height="9" rx="1.1"/><rect x="24.4" y="13" width="2.2" height="8" rx="1.1"/><rect x="37.4" y="13" width="2.2" height="8" rx="1.1"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M7.5 25 C4.5 11.5 9.5 1.5 14 3 C19 4.7 27 13 30.5 18.5 C24 23 15 25 7.5 25 Z"/><path d="M56.5 25 C59.5 11.5 54.5 1.5 50 3 C45 4.7 37 13 33.5 18.5 C40 23 49 25 56.5 25 Z"/><circle cx="32" cy="36" r="25"/></g><rect fill="${INK}" x="48" y="8" width="11" height="5" rx="2.5"/><rect fill="${INK}" x="52" y="17" width="10" height="5" rx="2.5"/>`,
  },
  penguin: {
    color: 'var(--penguin)',
    label: 'Penguin',
    role: 'dressed for dinner',
    line: 'rsvp yes to the dinner. black tie. i am already wearing it.',
    idle: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="23" cy="31" r="2.6"/><circle cx="41" cy="31" r="2.6"/><ellipse cx="32" cy="45" rx="4" ry="1.7"/><rect x="30.9" y="11" width="2.2" height="9" rx="1.1"/><rect x="24.4" y="13" width="2.2" height="8" rx="1.1"/><rect x="37.4" y="13" width="2.2" height="8" rx="1.1"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M7.5 25 C4.5 11.5 9.5 1.5 14 3 C19 4.7 27 13 30.5 18.5 C24 23 15 25 7.5 25 Z"/><path d="M56.5 25 C59.5 11.5 54.5 1.5 50 3 C45 4.7 37 13 33.5 18.5 C40 23 49 25 56.5 25 Z"/><circle cx="32" cy="36" r="25"/></g>`,
    talk: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="23" cy="30" r="2.6"/><circle cx="41" cy="30" r="2.6"/><ellipse cx="32" cy="47" rx="5" ry="6"/><rect x="30.9" y="11" width="2.2" height="9" rx="1.1"/><rect x="24.4" y="13" width="2.2" height="8" rx="1.1"/><rect x="37.4" y="13" width="2.2" height="8" rx="1.1"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M7.5 25 C4.5 11.5 9.5 1.5 14 3 C19 4.7 27 13 30.5 18.5 C24 23 15 25 7.5 25 Z"/><path d="M56.5 25 C59.5 11.5 54.5 1.5 50 3 C45 4.7 37 13 33.5 18.5 C40 23 49 25 56.5 25 Z"/><circle cx="32" cy="36" r="25"/></g><rect fill="${INK}" x="48" y="8" width="11" height="5" rx="2.5"/><rect fill="${INK}" x="52" y="17" width="10" height="5" rx="2.5"/>`,
  },
  pickles: {
    color: 'var(--pickles)',
    label: 'Pickles',
    role: 'professional good boy',
    line: 'note to self: the tennis ball is under the couch. this is urgent.',
    idle: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="24" cy="31" r="2.8"/><circle cx="40" cy="31" r="2.8"/><ellipse cx="32" cy="45" rx="5" ry="1.8"/><ellipse cx="32" cy="48.5" rx="2.8" ry="3.4"/></mask><g fill="${INK}" mask="url(#MASK)"><ellipse cx="10.5" cy="36" rx="7.5" ry="14"/><ellipse cx="53.5" cy="36" rx="7.5" ry="14"/><circle cx="32" cy="35" r="23"/></g>`,
    talk: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="24" cy="30" r="2.8"/><circle cx="40" cy="30" r="2.8"/><ellipse cx="32" cy="46" rx="6" ry="6"/></mask><g fill="${INK}" mask="url(#MASK)"><ellipse cx="10.5" cy="36" rx="7.5" ry="14"/><ellipse cx="53.5" cy="36" rx="7.5" ry="14"/><circle cx="32" cy="35" r="23"/></g><rect fill="${INK}" x="48" y="8" width="11" height="5" rx="2.5"/><rect fill="${INK}" x="52" y="17" width="10" height="5" rx="2.5"/>`,
  },
  olive: {
    color: 'var(--olive)',
    label: 'Olive',
    role: 'bow stays on',
    line: 'remind me: spa day thursday. the bow does not come off. ever.',
    idle: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="24" cy="31" r="2.8"/><circle cx="40" cy="31" r="2.8"/><ellipse cx="32" cy="45" rx="5" ry="1.8"/></mask><g fill="${INK}" mask="url(#MASK)"><ellipse cx="10.5" cy="36" rx="7.5" ry="14"/><ellipse cx="53.5" cy="36" rx="7.5" ry="14"/><circle cx="32" cy="35" r="23"/></g><path fill="${INK}" d="M50 16 C46 11 40 8.5 37.5 11.5 C35.5 14 38.5 18 44 19.5 C41 21.5 40.5 25 43 26.5 C46 28 49.5 25 50.5 20.5 Z"/><path fill="${INK}" d="M52 16 C56 10 62 7.5 64 10.5 C65.5 13 62.5 17.5 57 19 C60 21 60.5 24.5 58 26 C55 27.5 51.5 24.5 51 20 Z"/><circle fill="${INK}" cx="50.5" cy="18" r="4.6"/>`,
    talk: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="24" cy="30" r="2.8"/><circle cx="40" cy="30" r="2.8"/><ellipse cx="32" cy="46" rx="6" ry="6"/></mask><g fill="${INK}" mask="url(#MASK)"><ellipse cx="10.5" cy="36" rx="7.5" ry="14"/><ellipse cx="53.5" cy="36" rx="7.5" ry="14"/><circle cx="32" cy="35" r="23"/></g><path fill="${INK}" d="M50 16 C46 11 40 8.5 37.5 11.5 C35.5 14 38.5 18 44 19.5 C41 21.5 40.5 25 43 26.5 C46 28 49.5 25 50.5 20.5 Z"/><path fill="${INK}" d="M52 16 C56 10 62 7.5 64 10.5 C65.5 13 62.5 17.5 57 19 C60 21 60.5 24.5 58 26 C55 27.5 51.5 24.5 51 20 Z"/><circle fill="${INK}" cx="50.5" cy="18" r="4.6"/><rect fill="${INK}" x="5" y="8" width="11" height="5" rx="2.5"/><rect fill="${INK}" x="2" y="17" width="10" height="5" rx="2.5"/>`,
  },
};

export const ORDER: Animal[] = [
  'parrot', 'fox', 'bear', 'owl', 'cat',
  'dog', 'wolf', 'pig', 'panda', 'tiger',
  'frog', 'rabbit', 'hedgehog', 'penguin',
  'pickles', 'olive',
];

let _uid = 0;
export function nextId(): string {
  return 'f' + _uid++;
}

// Build a single <svg> frame for an animal + state with a unique mask id.
export function faceSVG(animal: Animal, state: 'idle' | 'talk', uid: string, cls = ''): string {
  const inner = FACES[animal][state].replace(/MASK/g, `${uid}-${state}`);
  return `<svg class="${cls}" viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">${inner}</svg>`;
}

// Both silhouette frames stacked (idle + talk) for a flap-able face.
export function framePair(animal: Animal, uid: string): string {
  return faceSVG(animal, 'idle', uid, 'idle') + faceSVG(animal, 'talk', uid, 'talk');
}

// The colored character, for anywhere the face is big enough to have a face.
// The silhouettes above stay for menu-bar-sized marks, where they are honest
// about what macOS actually renders up top.
export function characterSVG(animal: Animal, state: 'idle' | 'half' | 'talk', cls = ''): string {
  return `<svg class="${cls}" viewBox="${FACE_VIEWBOX}" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">${FACE_ART[animal][state]}</svg>`;
}

// All three colored frames stacked. The flap scripts move between them with
// the `mouth-half` and `mouth` classes so speech passes through a mid frame
// instead of snapping between two states.
export function characterFrames(animal: Animal): string {
  return (
    characterSVG(animal, 'idle', 'idle') +
    characterSVG(animal, 'half', 'half') +
    characterSVG(animal, 'talk', 'talk')
  );
}
