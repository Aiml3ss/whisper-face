# Deploying the Whisper Face site

Static Astro build hosted on **Cloudflare Pages** at **whisperface.com**.

## Cloudflare Pages via git integration (recommended)

Cloudflare builds on its own runners, so this does not spend GitHub Actions minutes.

1. Cloudflare dashboard → **Workers & Pages → Create → Pages → Connect to Git**.
2. Pick the `Aiml3ss/whisper-face` repo.
3. Build settings:
   - **Root directory:** `site`
   - **Build command:** `npm run build`
   - **Build output directory:** `dist`
   - **Framework preset:** Astro
4. Deploy. Then add the custom domain **whisperface.com** under the project's **Custom domains** tab and point DNS (Cloudflare will offer to manage it).

`_headers` (in `public/`) sets long cache on hashed assets and basic security headers. `wrangler.toml` records the Pages output dir.

## Manual deploy with Wrangler

```bash
cd site
npm run build
npx wrangler pages deploy dist --project-name whisper-face
```

## Before going live

- Point the **Download** buttons and GitHub links at the real release.
- Swap placeholder copy in `src/content/` and the components as needed.
