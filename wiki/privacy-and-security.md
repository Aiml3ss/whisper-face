---
title: "Privacy and Security"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [privacy, security, threat-model, receipts]
aliases: [privacy-promise, threat-model, support-bundle]
summary: "No account, no upload, no sale, no shared-model training; RAM-only audio; content-free receipts everywhere; seven security invariants; and a support bundle that is a double allowlist."
confidence: high
---

# Privacy and Security

## Definition

Whisper Face's data commitments (PRIVACY.md) and security boundaries
(SECURITY.md, docs/security/threat-model.md) are engineering
constraints, not marketing: audio is RAM-only, context is ephemeral
evidence, receipts are content-free, and every private file is local,
0600, and gitignored — with the honest note that local does not mean
anonymous.

## Key Properties

- **Never stored or logged**: raw audio, transcripts of other people,
  surrounding document text, keyword text in routine status, paths in
  readiness receipts, content-derived digests in recovery receipts,
  nearby destination text (only a SHA-256 fingerprint), spoken ceremony
  phrases.
- **RAM-only structures**: hold-to-talk and Flight Recorder buffers,
  the Voice Outbox, the acoustic time machine, the risky-action
  ceremony.
- **Disclosed local files**: transcripts.jsonl, learned.json,
  dictionary.txt, snippets.json, tones.json, preferences.json,
  voice_inbox.json, demonstrations.json, acoustic_keyword_memory.json,
  and the activation receipts — all private, atomic, 0600.
- **Seven security invariants** (threat model): cleanup may format
  evidence but never become acoustic truth; insertion honors the
  captured target with at most one attempt; ambiguous insertion is not
  retried and does not train personalization; audio is not persisted; a
  public release identifies one full Git commit with corresponding
  source; production artifacts are signed+notarized+stapled+checksummed
  with rollback metadata; new outbound flows require explicit consent.
- **Port 8787** is disclosed as unauthenticated, loopback by default,
  LAN only under explicit `--server-only`; binding/authenticating it is
  named open security work, alongside capture sandboxing, independent
  manifest signing, provenance attestations, egress regression tests,
  and external review.
- **Support bundle** (`support_bundle.py`): rebuilds a fixed schema from
  an already-allowlisted GUI snapshot — status enums, model
  family/status pairs, and last-result aggregates only; excludes
  transcripts, paths, usernames, hostnames, OS details, timestamps, and
  logs; written 0600 via a save panel and never uploaded.
- **Reporting**: private GitHub vulnerability reporting only, response
  targets stated as targets, safe harbor for good-faith research, seven
  named high-value areas (capture-after-stop, unexpected egress,
  endpoint access, focus confusion, supply chain,
  checksum/signature mismatch, over-broad permissions).

## Related Concepts

- [[insertion-transaction]], [[activation-receipt]],
  [[context-firewall]] — the invariants in mechanism form
- [[governance]] — the process that protects the commitments
- [[installers-and-services]] — the 0600/umask posture at install time

## References

- PRIVACY.md, SECURITY.md, docs/security/threat-model.md,
  support_bundle.py, SUPPORT.md
- [[2026-07-26-ops-governance-research]],
  [[2026-07-26-voice-actions-research]]
