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

## www redirect (dashboard, ~1 min)

`www.whisperface.com` isn't wired yet. Easiest fix in the Cloudflare dashboard:

- **Rules → Redirect Rules → Create** → *If* hostname equals `www.whisperface.com`, *then* dynamic redirect to `concat("https://whisperface.com", http.request.uri.path)`, 301.
- Or add `www.whisperface.com` as a second custom domain on the `whisper-face` Pages project (serves the same site, no redirect).

## Email hygiene (dashboard, optional but recommended)

Not using email on the domain? Publish "no mail" records so nobody can spoof `@whisperface.com`. Add in Cloudflare **DNS**:

```
TXT  @    "v=spf1 -all"
TXT  _dmarc   "v=DMARC1; p=reject; rua=mailto:you@example.com"
```

(If you *do* send mail from the domain later, replace these with real SPF/DKIM/DMARC.)

## Analytics (cookieless)

Cloudflare Web Analytics is wired but off. To enable: dashboard → **Web Analytics** → add `whisperface.com` → copy the beacon token → paste it into `CF_ANALYTICS_TOKEN` in `src/layouts/Base.astro`, rebuild, redeploy. No cookies, no banner needed.

## Before going live

- Point the **Download** / **Get it on GitHub** buttons at the real signed, notarized release once it exists (they currently go to the repo, and the install section says "building from source today").
- Swap placeholder copy in `src/content/` and the components as needed.
- Re-render `public/og.png` if the tagline changes.
