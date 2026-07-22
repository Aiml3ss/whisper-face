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

Residual risk: an opaque app can programmatically change its focused field
without user input while keeping the same window. The compatibility adapter
cannot observe that transition; it is intentionally narrower than the exact-AX
contract and must not be generalized to arbitrary applications.
