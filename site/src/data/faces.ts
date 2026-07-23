// The five Whisper Face faces, as inline SVG.
// Each face has an `idle` and a `talk` frame (mouth open + little sound bars).
// "MASK" is a placeholder swapped for a unique id per rendered instance so
// multiple faces on one page never collide on the same <mask> id.

const INK = '#0B0F0D';

export type Animal = 'fox' | 'bear' | 'owl' | 'parrot' | 'cat';

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
    idle: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="24" cy="31" r="2.8"/><circle cx="40" cy="31" r="2.8"/><ellipse cx="32" cy="45" rx="5" ry="1.8"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M7 25 14 2l17 17zM57 25 50 2 33 19z"/><circle cx="32" cy="35" r="25"/><path d="m17 48 15 14 15-14z"/></g>`,
    talk: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="24" cy="30" r="2.8"/><circle cx="40" cy="30" r="2.8"/><ellipse cx="32" cy="46" rx="6" ry="6"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M7 25 14 2l17 17zM57 25 50 2 33 19z"/><circle cx="32" cy="35" r="25"/><path d="m17 48 15 14 15-14z"/></g><path fill="${INK}" d="M48 8h11v5H48zM52 17h10v5H52z"/>`,
  },
  bear: {
    color: 'var(--bear)',
    label: 'Bear',
    role: 'calm & steady',
    line: 'dear team, the new build ships tonight. no typos, i promise.',
    idle: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="23" cy="31" r="2.5"/><circle cx="41" cy="31" r="2.5"/><ellipse cx="32" cy="46" rx="4" ry="1.7"/></mask><g fill="${INK}" mask="url(#MASK)"><circle cx="14" cy="14" r="11"/><circle cx="50" cy="14" r="11"/><circle cx="32" cy="36" r="26"/></g>`,
    talk: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="23" cy="30" r="2.5"/><circle cx="41" cy="30" r="2.5"/><ellipse cx="32" cy="47" rx="5" ry="6"/></mask><g fill="${INK}" mask="url(#MASK)"><circle cx="14" cy="14" r="11"/><circle cx="50" cy="14" r="11"/><circle cx="32" cy="36" r="26"/></g><path fill="${INK}" d="M48 7h11v5H48zM52 16h10v5H52z"/>`,
  },
  owl: {
    color: 'var(--owl)',
    label: 'Owl',
    role: 'wide awake',
    line: 'def clean(text): return text.strip()  # yep, it does code too',
    idle: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="23" cy="32" r="7"/><circle cx="41" cy="32" r="7"/><path d="m28 45 4-3 4 3-4 2z"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M6 25 15 2l16 17zM58 25 49 2 33 19z"/><circle cx="32" cy="36" r="26"/><path d="M14 49h36l-7 13H21z"/></g>`,
    talk: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="23" cy="31" r="7"/><circle cx="41" cy="31" r="7"/><path d="m25 44 7-5 7 5-7 9z"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M6 25 15 2l16 17zM58 25 49 2 33 19z"/><circle cx="32" cy="36" r="26"/><path d="M14 49h36l-7 13H21z"/></g><path fill="${INK}" d="M48 8h11v5H48zM52 17h10v5H52z"/>`,
  },
  parrot: {
    color: 'var(--parrot)',
    label: 'Parrot',
    role: 'the original',
    line: 'hold a key, say the thing, and it just shows up at your cursor',
    idle: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="38" cy="23" r="3"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M10 47 3 61l16-10zM28 7A24 24 0 1 0 28 55c15 0 25-9 25-23C53 18 43 7 28 7Z"/><path d="m44 21 17 9-17 6zM47 10l8-5 3 5-9 4z"/></g>`,
    talk: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="38" cy="23" r="3"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M10 47 3 61l16-10zM28 7A24 24 0 1 0 28 55c15 0 25-9 25-23C53 18 43 7 28 7Z"/><path d="m44 19 17 7-17 4zM44 33l16 5-16 5zM47 9l8-5 3 5-9 5zM51 16h11v4H51z"/></g>`,
  },
  cat: {
    color: 'var(--cat)',
    label: 'Cat',
    role: 'quietly judging',
    line: 'buy oat milk. cancel the gym. book the flight. sigh.',
    idle: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="23" cy="31" r="2.5"/><circle cx="41" cy="31" r="2.5"/><ellipse cx="32" cy="46" rx="4" ry="1.7"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M7 24 13 2l18 17zM57 24 51 2 33 19z"/><circle cx="32" cy="36" r="25"/></g><path fill="${INK}" d="M15 43H1v3h14zM49 43h14v3H49zM14 50 1 54l1 3 14-4zM50 50l13 4-1 3-14-4z"/>`,
    talk: `<mask id="MASK"><rect width="64" height="64" fill="#fff"/><circle cx="23" cy="30" r="2.5"/><circle cx="41" cy="30" r="2.5"/><ellipse cx="32" cy="47" rx="5" ry="6"/></mask><g fill="${INK}" mask="url(#MASK)"><path d="M7 24 13 2l18 17zM57 24 51 2 33 19z"/><circle cx="32" cy="36" r="25"/></g><path fill="${INK}" d="M15 42H1v3h14zM49 42h14v3H49zM14 49 1 53l1 3 14-4zM50 49l13 4-1 3-14-4zM48 6h11v5H48z"/>`,
  },
};

export const ORDER: Animal[] = ['parrot', 'fox', 'bear', 'owl', 'cat'];

let _uid = 0;
export function nextId(): string {
  return 'f' + _uid++;
}

// Build a single <svg> frame for an animal + state with a unique mask id.
export function faceSVG(animal: Animal, state: 'idle' | 'talk', uid: string, cls = ''): string {
  const inner = FACES[animal][state].replace(/MASK/g, `${uid}-${state}`);
  return `<svg class="${cls}" viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">${inner}</svg>`;
}

// Both frames stacked (idle + talk) for a flap-able face.
export function framePair(animal: Animal, uid: string): string {
  return faceSVG(animal, 'idle', uid, 'idle') + faceSVG(animal, 'talk', uid, 'talk');
}
