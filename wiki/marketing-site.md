---
title: "Marketing Site"
type: concept
language: en
created: 2026-07-26
modified: 2026-07-26
tags: [site, astro, web, cloudflare]
aliases: [whisperface-com, site]
summary: "A fully static Astro 5 + Tailwind 4 site at whisperface.com — docs, blog, and the sixteen characters as generated inline SVG with a three-frame flap — deployed by Cloudflare Pages, not Actions."
confidence: high
---

# Marketing Site

## Definition

`site/` is a static Astro 5 site (Tailwind 4, sitemap) for
whisperface.com: a landing page (Nav, Hero, Marquee, Features,
HowItWorks, FacesGallery, PrivacyBand, Install, Faq, Footer), four docs
pages, and a small blog.

## Key Properties

- **Faces on the web**: the sixteen characters ship as generated inline SVG
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
  in THIRD_PARTY_NOTICES.md. The theme toggle is a real `jelly-button`
  wearing the site's clothes, with a `:not(:defined)` fallback if the CDN
  fails.
- **Downloads**: a single constant, `site/src/data/release.ts`, holds the
  current version, tag, DMG URL and size; every download link on the site
  reads from it, and an `unsigned` flag drives an honest Gatekeeper
  warning ([[distribution]]).
- DEPLOY.md keeps an honest pre-launch TODO list (www redirect, no-mail
  SPF/DMARC records); its download-button item is now stale — that button
  has pointed at a real release DMG since #97.

## One motion vocabulary, two renderers

> 📝 **Updated from [[2026-07-26-interface-rebuild-research]]** (#111):
> `site/src/data/motion.ts` mirrors `whisper_face_theme.MOTION_SPECS`
> exactly — the same four springs and all 28 constants — integrates the
> same second-order ODE Core Animation solves, and bakes the result into
> `@keyframes` on the standalone `scale` property, so the sticker push
> and face tilts keep `transform`. `site/src/data/jelly.ts` is the
> trigger: press on `pointerdown`, release on `pointerup`, and a hard
> Reduce Motion bail that returns without touching the element.
>
> This hybrid exists because **Jelly UI exposes no physics API** — its
> springs are baked per component and its body is painted into a canvas
> in its shadow root — while the Mac app translates the same specs into
> `CASpringAnimation`. Parity therefore means both surfaces running the
> same named motions with the same numbers, not sharing a library. Reduce
> Motion is gated in three layers, including a `data-jelly-motion` flag
> stamped on `<html>` so the guarantee is auditable in the served HTML
> and the library's own canvas physics is gated too.
>
> Two 404 bugs surfaced on the way: the owl had collapsed to nothing
> because a page-scoped `.face-frames` rule never matched the span
> `Face.astro` renders, and an inline `padding` shorthand was zeroing
> `.wrap`'s gutter on phones.

## Related Concepts

- [[whisper-faces]] — the art pipeline feeding the site
- [[design-language]] — the springs and palettes both surfaces share
- [[governance]] — why no Actions deploy
- [[distribution]] — the releases the download button points at
- [[whisper-face]] — the product the site sells

## References

- site/ (astro.config.mjs, src/, DEPLOY.md, wrangler.toml,
  src/data/motion.ts, src/data/jelly.ts, src/data/release.ts)
- [[2026-07-26-ops-governance-research]],
  [[2026-07-26-interface-rebuild-research]]
