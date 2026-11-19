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

The repository's own beacon is off (`CF_ANALYTICS_TOKEN` is empty). Production
is not: as observed on 14 November 2026, the Cloudflare dashboard injects a Web
Analytics beacon with its own token, and a Cloudflare-managed robots.txt that
blocks several crawlers replaces `public/robots.txt`. Both are dashboard
settings the source cannot see; the site's privacy page says the beacon was on.
Turning either off, or keeping it, is an owner decision made in the dashboard.
To use the repository's beacon instead: dashboard → **Web Analytics** → add `whisperface.com` → copy the beacon token → paste it into `CF_ANALYTICS_TOKEN` in `src/layouts/Base.astro`, rebuild, redeploy. No cookies, no banner needed.

## Before going live

- The download buttons already point at a real release: `src/data/release.ts` holds the version, tag, DMG, size, release-notes and SHA256SUMS URLs, and every link on the site reads from there. Update those values when cutting a release.
- From 0.7.1 the Mac build is signed with the Developer ID, notarized and stapled, so `RELEASE.unsigned` is `false` and the install section shows no Open Anyway note. Releases up to 0.7.0 were ad-hoc signed and not notarized; set it back to `true` only for a build that is not notarized.
- `size` and `windowsSize` read `SIZE-PENDING` until the assets are uploaded; replace them with the published sizes before deploying.
- Swap placeholder copy in `src/content/` and the components as needed.
- Re-render `public/og.png` if the tagline changes.
