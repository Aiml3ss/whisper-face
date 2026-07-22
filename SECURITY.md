# Security policy

Whisper Face controls a microphone, reads bounded application context, and can
insert text into another application. Security and privacy failures in those
boundaries are treated as product failures, not merely bugs.

## Supported versions

Security fixes are made on the `main` branch and the newest published release.
Older builds may not receive backports. The exact source revision for a running
build is available from **Diagnostics -> Exact Source** and from `GET /source`.

## Privately report a vulnerability

Do not open a public issue for an unpatched vulnerability or include another
person's audio, transcript, credentials, private context, or recovery text in a
report.

Use GitHub's private vulnerability-reporting form:

<https://github.com/Aiml3ss/whispering-parrot/security/advisories/new>

Include the affected revision, macOS or Windows version, reproduction steps,
impact, and the least-sensitive evidence that demonstrates the problem. Please
say whether the issue is known to be actively exploited.

The project will try to acknowledge a complete report within three business
days, provide an initial severity assessment within seven business days, and
coordinate a fix and disclosure date with the reporter. These are response
targets rather than warranties. A critical, reproducible issue may require an
immediate release or temporary feature disablement.

## Research guidelines

Good-faith research should use accounts, machines, speech, and documents you
own or have explicit permission to test. Stop if testing could expose another
person's data or disrupt a service. Do not use social engineering, persistence,
destructive payloads, denial of service, or public disclosure before a fix is
available. The project will not pursue action for research that follows these
guidelines, avoids privacy harm, and is reported promptly, to the extent the
Project Owner can make that commitment.

## High-value report areas

- capture continuing after the UI says it stopped;
- audio, transcript, context, correction, or clipboard data leaving the device
  unexpectedly;
- unauthenticated access to the transcription endpoint outside its intended
  trusted network;
- focus/selection confusion, duplicate insertion, or insertion into the wrong
  application;
- update, installer, model, or dependency supply-chain compromise;
- release checksum, signature, notarization, or source-revision mismatch;
- local private files created with permissions broader than the current user.

The public trust boundaries and current mitigations are documented in the
[threat model](docs/security/threat-model.md). The user-facing data contract is
the [privacy promise](PRIVACY.md).
