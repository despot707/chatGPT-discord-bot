# Static community assistant site

This is a dependency-free static website for Cloudflare Pages. The site source contains no account system, checkout, forms, JavaScript, analytics, cookies, or third-party assets. Its branding and links live in `site.config.json`.

## Build and verify

Requires Node.js 20 or newer. From this directory run:

```sh
npm run build
npm run verify
```

`npm test` runs both commands in sequence for continuous integration.

The build reads `PRIVACY.md`, `TERMS.md`, and `docs/prepaid-plans.md` from the repository root so public policy text and allowance tables stay connected to their source. It writes only to `site/dist/`, which is ignored by Git. The output is safe to publish as the Cloudflare Pages artifact.

## Cloudflare Pages

From this directory, build the static output and publish it to the prepared Cloudflare Pages project:

```sh
npm run build
npx wrangler@4.144.0 pages deploy dist --project-name discord-community-assistant --branch main
```

The deploy command publishes the already-built `dist/` directory. No Wrangler dependency or server runtime is needed in the website package. The included `_headers` file applies static security headers.

The site display name is `Sidecord Ai`, matching the Discord Developer Portal. The canonical base URL is `https://sidecord-ai.com`. The configured invite link uses the app’s reviewed guild-install scopes and permissions. The production config currently sets `saleStatus` to `live`, which displays purchase links for the three reviewed Discord SKUs. The site source also holds the reviewed application and SKU IDs; the build rejects any mismatch before creating live purchase links. Live plan links go to Discord’s native Store. For preview builds, set `saleStatus` to `coming_soon`; test runs may override the configured status with `NODE_ENV=test` and `SITE_SALE_STATUS`, while that override is ignored outside the test environment.

The support URL points to the public repository issue tracker. It is a user-initiated external link, not an embedded form. Tell users not to post credentials, tokens, private messages, or other sensitive information in public issues.

## Routes

- `/` landing page
- `/plans/` plan preview
- `/privacy/` application privacy policy plus a distinct static website disclosure
- `/terms/` application terms
- `/support/` in-app controls and support guidance
- `/404.html` missing-page guidance
