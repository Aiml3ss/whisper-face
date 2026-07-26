---
title: "Marketing Site"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [site, astro, web, cloudflare]
aliases: [whisperface-com, site]
summary: "A fully static Astro 5 + Tailwind 4 site at whisperface.com — docs, blog, and the ten characters as generated inline SVG with a three-frame flap — deployed by Cloudflare Pages, not Actions."
confidence: high
---

# Marketing Site

## Definition

`site/` is a static Astro 5 site (Tailwind 4, sitemap) for
whisperface.com: a landing page (Nav, Hero, Marquee, Features,
HowItWorks, FacesGallery, PrivacyBand, Install, Faq, Footer), four docs
pages, and a small blog.

## Key Properties

- **Faces on the web**: the ten characters ship as generated inline SVG
  (idle / half / talk frames) from the shared spec — the hero syncs the
  mouth to the letters landing in the ticker and the gallery runs a
  babble loop on hover ([[whisper-faces]]). Menu-bar-sized marks keep
  the honest hand-authored silhouettes.
- **Deploy**: Cloudflare Pages project `whisper-face` via the Git
  integration building `site/` — deliberately no GitHub Actions
  workflow, which also sidesteps the Actions budget ([[governance]]).
  Manual path: `npx wrangler pages deploy dist`.
- **Headers**: immutable 1-year caching for hashed assets plus
  nosniff / strict referrer / same-origin frame headers. Cloudflare Web
  Analytics is wired but off (empty token, cookieless).
- **Third-party**: Jelly UI from its CDN with an SRI hash pin recorded
  in THIRD_PARTY_NOTICES.md.
- DEPLOY.md keeps an honest pre-launch TODO list (download buttons,
  www redirect, no-mail SPF/DMARC records).

## Related Concepts

- [[whisper-faces]] — the art pipeline feeding the site
- [[governance]] — why no Actions deploy
- [[whisper-face]] — the product the site sells

## References

- site/ (astro.config.mjs, src/, DEPLOY.md, wrangler.toml)
- [[2026-07-26-ops-governance-research]]
