---
title: "Voice Objects"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [actions, drafts, inbox, inert, macos]
aliases: [voice-inbox, voice-object-commands]
summary: "Typed spoken commands become inert local drafts in a durable inbox; explicit reveal-then-confirm ceremonies can copy a draft or request a Mail compose window — and nothing can send, schedule, or automate."
confidence: high
---

# Voice Objects

## Definition

Voice Objects are the first interaction layer beyond paste: an
off-by-default macOS Privacy setting diverts three exact spoken forms —
`create task: <title>`, `draft email to <contact>: <body>`,
`create calendar event <ISO start>: <title>` — into inert typed drafts
queued in a private local inbox (`voice_inbox.json`). All other speech
follows the normal paste path.

## Key Properties

- **Pure projection**: `voice_objects.py` converts closed-role facts
  (SUMMARY, DETAILS, CONTACT, WHEN, END) into exactly one of four inert
  drafts with content-free receipts; conflicting facts reject rather
  than guess.
- **Strict grammar**: the parser accepts the three case-sensitive forms
  and nothing else — a rejection is never a best-effort interpretation,
  and parser rejection is deliberately indistinguishable from a store
  failure so ordinary paste is the uniform fallback.
- **Durable inbox**: single-owner queue with closed-schema JSON, atomic
  0600 writes, idempotent enqueue, and bounded everything (256 items,
  100k chars). Listings return only id/sequence/destination/state. Since
  2026-07-26 the [[menu-bar]] **Voice Inbox** row appears only while
  drafts are queued — still count-only — and the drafts themselves are
  inspected from the Voice Object Commands row on Settings → Privacy
  ([[app-window]]), whose empty state reads "No local drafts are stored…
  inert until you act on it."
- **Bridge validation**: payloads round-trip through re-projection on
  decode — a payload that could not have come from a legal projection is
  rejected.
- **Reveal then confirm**: draft text is read only after an explicit
  Reveal. A second confirmation mints a fresh single-use nonce and
  re-reads the item: email drafts can request the native Mail compose
  sheet (the adapter binds no send API — opening the sheet is the entire
  capability); task/calendar drafts can copy to the clipboard, with a
  change-count-guarded Clear afterwards. Email is not copyable;
  task/calendar are not composable; rejections burn the nonce.
- **Deliberately impossible**: sending, scheduling, app automation,
  content in receipts/logs/argv/URLs, silent retries, unexplicit reads.
  Turning the setting off stops new diversion but leaves queued drafts
  intact.

## Related Concepts

- [[voice-modes]] — where the diversion sits (capture mode, before
  cleanup and insertion)
- [[inert-foundations]] — the sibling foundations and the shared
  "no surprise execution" shape
- [[privacy-and-security]] — the storage and receipt posture

## References

- voice_objects.py, voice_inbox.py, voice_object_command_parser.py,
  voice_object_inbox_bridge.py, macos_email_compose.py,
  macos_voice_draft_clipboard.py
- [[2026-07-26-voice-actions-research]]
