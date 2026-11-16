# ADR-0003: Final insertion is an exactly-once local transaction

Status: accepted

## Context

Recognition and cleanup continue after hotkey release. During that time the
user can change applications, windows, fields, selections, or nearby text. A
blind paste can therefore corrupt the wrong destination, while retrying an
ambiguous paste can duplicate content.

## Decision

On Mac, capture an Insertion Lease at hotkey press. Readable fields bind the
focused element, selection, and a hash of bounded nearby text. Fields that hide
their value still bind the focused element. A reviewed compatibility adapter
for the OpenAI ChatGPT and Codex apps may instead bind the frontmost process
and window, captures text-free keyboard, mouse, drag, movement, and scroll
counters at hotkey press, then seals that baseline at release. It permits one
unverified paste only if the same window is still frontmost and no input
occurred from capture through recognition. Unknown apps, unavailable counters,
window drift, or input activity fail closed into the Voice Outbox. Readback has
a 20 ms bound and produces an Insertion Receipt. Windows retains its existing
insertion path until an equivalent native destination adapter exists.

Verified payload text is erased immediately, with only a bounded receipt
tombstone retained for deduplication. Non-verified terminal results enter the
bounded Voice Outbox; in-flight results are never recoverable or dismissible.
Clipboard recovery acknowledges an item only after macOS confirms the copy.

Optional delayed capture cleanup is a second, separately gated transaction
after the initial insertion is verified. It compares the original insertion,
the Voice Compiler-proofed proposal, and two fresh focused-destination
snapshots; it merges only untouched spans and consumes each proposal and final
revision once. The macOS Accessibility API has no native atomic
compare-and-swap, so the adapter performs one last exact closed-observation
comparison immediately before its single whole-value write. This residual
read-to-write scheduling window requires caller-attested physical evidence
before a local activation receipt can enable the path. Missing or invalid
evidence leaves synchronous behavior unchanged.

## Consequences

- Focus, selection, and nearby-text drift cannot silently redirect readable
  Mac insertions.
- An unavailable focused Accessibility element fails closed unless a reviewed
  compatibility adapter is active; compatibility results remain explicitly
  unverified and are never used for learning.
- Ambiguous delivery is visible and never retried or used for learning.
- Recovery is explicit; attempted-unknown items warn that text may already be
  present at the destination.
- Receipt proof adds at most 20 ms beyond the existing paste operation.
- Delayed cleanup cannot run before a verified initial insertion, and
  scheduled delayed utterances do not enter correction learning.

Residual risk: an opaque app can programmatically change its focused field
without user input while keeping the same window. The compatibility adapter
cannot observe that transition; it is intentionally narrower than the exact-AX
contract and must not be generalized to arbitrary applications.

## Amendment, 14 November 2026: readback timing

The 20 ms bound in the Decision and Consequences above no longer holds, and
has not since 23 July 2026, when Electron destinations were given a separate
0.35 s window while native apps kept 20 ms. As of the November 2026 audit
fixes, every app gets the same adaptive readback window: the readback returns
on the first read that matches and otherwise polls for up to 0.4 s
(`READBACK_TIMEOUT` in `dictate.py`). A matching readback still costs one read;
a slow or non-matching destination can add up to 0.4 s before the receipt is
settled. The per-surface constants are gone.

Two related changes in the same fixes: Accessibility ranges are converted from
UTF-16 units to code points at the boundary, so offsets past an emoji are
correct, and each Insertion Receipt now carries the shape of its own readback
(`readback_shape`) instead of the runtime reading it from shared state. The
rest of this decision is unchanged.

## Amendment, 15 November 2026: two more proofs

A readback that reads the paste back in another whitespace form (a no-break
space for a space, or a line break written as CR LF, CR, U+2028, U+2029 or
U+0085) is verified with its own reason, `commit_verified_whitespace_form`.
These are the same text by construction; no other difference was loosened.

A field leased by its element that hides its text but reports a selection
range is checked by position: after the paste its range is polled for up to
0.12 s, and a bare cursor exactly the paste's UTF-16 length past the
selection's start, with any character count the element offers agreeing, is
verified as `commit_verified_position`. That proves the right amount went in at
the right place, not which characters, so the receipt is shown and kept like
any verified one but never used for undo, delayed cleanup or learning. Anything
short of it is the unverifiable receipt it was. Both proofs, and every
conflict, record content-free categories of what the readback saw.
