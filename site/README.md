# Static community assistant site

This is a dependency-free static website for Cloudflare Pages. It contains no account system, checkout, forms, JavaScript, analytics, cookies, or third-party assets. Its branding and links live in `site.config.json`; the default display name is temporary and can be renamed there.

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

Before public launch, replace the temporary brand in `site.config.json`, set `baseUrl` to the final HTTPS origin, and set `inviteUrl` only after verifying the bot's actual install link. Keep `saleStatus` as `coming_soon`; the build fails if it changes because this website has no checkout flow. After changing these values, rebuild and verify before deploying.

The support URL points to the public repository issue tracker. It is a user-initiated external link, not an embedded form. Tell users not to post credentials, tokens, private messages, or other sensitive information in public issues.

## Routes

- `/` landing page
- `/plans/` plan preview
- `/privacy/` application privacy policy plus a distinct static website disclosure
- `/terms/` application terms
- `/support/` in-app controls and support guidance
- `/404.html` missing-page guidance
