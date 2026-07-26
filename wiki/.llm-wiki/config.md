# Wiki Configuration

This document uses a standard YAML block for settings. The frontmatter
between `---` markers is machine-parseable; the surrounding markdown
provides human-readable documentation.

---

## Wiki Settings
wiki_name: "Whisper Face Wiki"
wiki_root: "./wiki"
language: "en"              # en | zh | bilingual

## Ingest Settings
auto_index: true             # Auto-regenerate index after each change
two_phase: true              # Use two-phase ingest (Phase 1 analysis, Phase 2 generation)
require_review: false        # Owner-approved bulk build (2026-07-26); analysis kept in inbox/

## Query Settings
max_pages_to_read: 5         # Maximum pages to read per query
prefer_language_match: true  # Prefer pages in query language

## Lint Settings
lint_on_startup: false       # Run quick lint on session start
full_lint_frequency: 10      # Suggest full lint every N ingests

---

To change a setting, edit the value after the colon. Lines beginning
with `#` are comments and are ignored.
