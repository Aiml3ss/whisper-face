# Licensing and contribution release process

This process protects the AGPL community edition, the commercial licensing
option, and the historical MIT grant.

## Release rules

1. Keep the canonical `AGPL-3.0-only` text in root `LICENSE`.
2. Keep the historical MIT text in `LICENSES/MIT.txt` and never describe the
   MIT grants for snapshots through full commit
   `8f317df7ac5bb687ac8fbbfcd23abc1385be396d` as revoked.
3. Treat [LICENSE_POLICY.md](../LICENSE_POLICY.md) as the scope authority for
   first-party and third-party material.
4. Maintainers and agents must not merge an outside Contribution until the
   repository-controlled acceptance ledger records the contributor's GitHub
   login and immutable numeric user ID, CLA version and checksum, date, and
   evidence reference.
   The check must read the ledger from the pull request's base revision, never
   trust a ledger change supplied by the contributor branch, and permit the
   declared Project Owner without a self-CLA.
5. Record provenance and licensing for every new dependency, model, asset,
   font, or copied/generated third-party work in
   [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
   Model revisions must be immutable in the preload path; if an upstream tag
   cannot be pinned, record its audited content digest and fail review when it
   changes.
6. Do not promise commercial rights, pricing, warranties, trademark rights, or
   patent protection without a written agreement approved by the Project
   Owner and, where appropriate, counsel.
7. Keep the CLA check required in GitHub and run
   `uv run tests/test_repository_governance.py` on every pull request.
8. Keep the native **License Notices** and **Exact Source** controls plus the
   network `GET /source` and `GET /license` responses accurate. Source offers
   must contain the running immutable commit, not a moving branch. A packaged
   or modified deployment must set `WHISPER_FACE_SOURCE_URL` and
   `WHISPER_FACE_SOURCE_REVISION` for its corresponding source.

## Changing this structure

Changes to the outbound license, CLA, historical transition boundary,
commercial-license policy, trademark policy, or Project Owner require explicit
Project Owner approval. A contributor may propose such a change but an agent
or maintainer must not infer approval from an unrelated implementation task.

This operational checklist is not legal advice. The Project Owner should have
qualified counsel review the CLA and commercial agreement before accepting
material outside contributions or signing the first commercial deal.
