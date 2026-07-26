# Hot Cache / 热缓存
**Last session:** 2026-07-26 (delta ingest — interface rebuild + evidence capture)

## Recent Activity / 最近活动
| Date | Operation | Source |
|------|-----------|--------|
| 2026-07-26 | ingest | `.raw/` research briefs (whole-app wiki build, 33 pages) |
| 2026-07-26 | ingest | `.raw/interface-rebuild-research.md` (#101, #104, #105, #111, #112) |
| 2026-07-26 | ingest | `.raw/evidence-capture-research.md` (#107, #109, issues #108/#110, v0.1.0–v0.2.1) |

Wiki is now **39 pages**: 32 concept, 6 article, 1 synthesis (plus the
`index.md` symlink). Zero broken links, zero orphans.

## Pending Review / 待审核
*No pending reviews.* Both delta ingests superseded stale surface
descriptions in place rather than raising contradictions; the analyses in
`inbox/` record what was superseded and why.

## Active Topics / 活跃主题
- **New pages**: [[menu-bar]], [[app-window]], [[design-language]],
  [[evidence-capture]].
- **Known blockers now recorded**: issues #108 and #110 — calibration,
  keyword priority and delayed cleanup all require A/B evidence whose
  candidate arm the runtime only produces *after* the receipt that
  evidence would authorize. Carried on [[activation-receipt]],
  [[delayed-cleanup]] and [[acoustic-personalization]].
- **Known gaps carried forward**: SpanGraph has no class
  ([[voice-compiler]]); the consequence-routing corpus has no `verified`
  case ([[benchmarks]]); Reduce Motion is polled rather than observed
  ([[design-language]]).
- **Not asserted**: that the repository was private before 2026-07-26 —
  unverifiable from the API ([[governance]]).
- **Doc-vs-code drift found, not fixed** (this wiki touches no code):
  `README.md:437` and `:497` describe the pre-#101 menu; `site/DEPLOY.md:52`
  says the download buttons point at the repo.
