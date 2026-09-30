import { cp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const site = path.resolve(here, '..');
const repo = path.resolve(site, '..');
const out = path.join(site, 'dist');
const read = (relative) => readFile(path.join(repo, relative), 'utf8');
const config = JSON.parse(await readFile(path.join(site, 'site.config.json'), 'utf8'));
const saleStatus = process.env.NODE_ENV === 'test' ? (process.env.SITE_SALE_STATUS || config.saleStatus) : config.saleStatus;
const expectedCheckout = {
  applicationId: '1365724363722068120',
  skus: {
    basic: '1554920142532513832',
    plus: '1554920641088593990',
    premium: '1554920977488551936',
  },
};

for (const [name, value] of Object.entries({ baseUrl: config.baseUrl, supportUrl: config.supportUrl, sourceUrl: config.sourceUrl, ...(config.inviteUrl ? { inviteUrl: config.inviteUrl } : {}) })) {
  const parsed = new URL(value);
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password) throw new Error(`${name} must be a public HTTPS URL`);
}
if (!['coming_soon', 'live'].includes(saleStatus)) throw new Error('SITE_SALE_STATUS must be coming_soon or live');
if (JSON.stringify(config.checkout) !== JSON.stringify(expectedCheckout)) throw new Error('Checkout configuration does not match the reviewed Discord application and plan SKUs');
if (config.inviteUrl) {
  const invite = new URL(config.inviteUrl);
  if (invite.origin !== 'https://discord.com'
    || invite.searchParams.get('client_id') !== expectedCheckout.applicationId
    || invite.searchParams.get('scope') !== 'bot applications.commands'
    || invite.searchParams.get('permissions') !== '274878024704'
    || invite.searchParams.get('integration_type') !== '0') {
    throw new Error('Discord invite URL does not match the reviewed Sidecord Ai installation settings');
  }
}

const live = saleStatus === 'live';
const storeUrl = `https://discord.com/application-directory/${config.checkout.applicationId}/store`;
const productUrl = (product) => `${storeUrl}/${config.checkout.skus[product]}`;

function escapeHtml(value) {
  return value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}

function inlineMarkdown(value) {
  let safe = escapeHtml(value);
  safe = safe.replace(/\[([^\]]+)\]\((https:\/\/[^\s)]+)\)/g, '<a href="$2" rel="noopener noreferrer">$1</a>');
  safe = safe.replaceAll(escapeHtml(config.supportUrl), `<a href="${escapeHtml(config.supportUrl)}" rel="noopener noreferrer">${escapeHtml(config.supportUrl)}</a>`);
  safe = safe.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  return safe;
}

function markdownBody(markdown) {
  const blocks = [];
  let paragraph = [];
  const flush = () => {
    if (paragraph.length) blocks.push(`<p>${paragraph.map(inlineMarkdown).join(' ')}</p>`);
    paragraph = [];
  };
  for (const line of markdown.split(/\r?\n/)) {
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flush();
      const level = Math.min(3, heading[1].length + 1);
      blocks.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
    } else if (!line.trim() || line.startsWith('---')) flush();
    else paragraph.push(line.trim());
  }
  flush();
  return blocks.join('\n');
}

function markdownTable(markdown, heading) {
  const section = markdown.split(heading)[1]?.split(/^## /m)[0] ?? '';
  const rows = section.split(/\r?\n/).filter((line) => line.startsWith('|')).map((line) => line.split('|').slice(1, -1).map((cell) => cell.trim()));
  if (rows.length < 3) throw new Error(`Could not parse ${heading} table from prepaid-plans.md`);
  return rows.filter((row) => !row.every((cell) => /^:?-{3,}:?$/.test(cell))).map((row) => row.map((cell) => cell.replaceAll('**', '')));
}

const privacyMd = await read('PRIVACY.md');
const termsMd = await read('TERMS.md');
const pricingMd = await read(config.pricing.source);
const plans = markdownTable(pricingMd, '## Included allowances');
const planHeaders = plans[0];
const planRows = plans.slice(1);

function allowanceRows(headers, values) {
  return headers.slice(2, 6).map((header, index) => `<div class="allowance"><span>${escapeHtml(header)}</span><span>${escapeHtml(values[index + 2])}</span></div>`).join('');
}

const planCards = planRows.map((row, index) => `<article class="plan${index === 1 ? ' featured' : ''}"><div class="plan-label">${index === 1 ? 'More AI features' : index === 0 ? 'AI starts here' : 'Highest AI allowances'}</div><h2>${escapeHtml(row[0])}</h2><div class="price">${escapeHtml(row[1])}<small> / server / month</small></div><p>${live ? 'Monthly Discord guild subscription. The allowance is shared across this server.' : 'Proposed finite AI allowance per eligible period.'}</p><div class="allowances">${allowanceRows(planHeaders, row)}</div>${live ? `<a class="button button-primary plan-purchase" href="${productUrl(row[0].toLowerCase())}" rel="noopener noreferrer">View ${escapeHtml(row[0])} on Discord ↗</a>` : ''}</article>`).join('');

const homeAiCopy = live
  ? 'Optional AI plans are available as monthly Discord subscriptions for your server. Ask questions, discuss screenshots, search the web, or generate an image within the plan allowances.'
  : 'Free profiles and game-night tools for your Discord community. Paid AI chat, screenshot understanding, and image generation are coming soon.';
const homePlanAside = live
  ? '<strong>AI is optional</strong>Paid plans are monthly guild subscriptions managed through Discord. Free profiles and game-night tools remain available.'
  : '<strong>AI is optional</strong>Premium AI plans are not available for purchase yet. Prices and allowances are previews.';
const plansIntro = live
  ? 'Profiles and code-only game, party, and team features stay free. Paid plans are monthly Discord guild subscriptions. Each plan allowance is shared by members in that server.'
  : 'Profiles and code-only game, party, and team features are part of an ongoing free tier. There is no trial and no free AI credit. The plans below are proposed AI allowances, priced per server in USD.';
const plansNotice = live
  ? '<div class="notice" role="status"><strong>Subscribe through Discord.</strong> Each monthly allowance is shared across the server. Cancel any time in Discord subscription settings; access continues through the current paid period. Unused allowance does not roll over, and there are no overage charges or automatic refills.</div>'
  : '<div class="notice" role="status"><strong>Premium AI plans are not available for purchase yet.</strong> Prices and allowances are previews. No purchase or charge can occur in this preview.</div>';
const plansTerms = live
  ? '<p>Allowances are finite and shared by members of the subscribed server for each authenticated Discord billing period. Discord renews subscriptions automatically unless cancelled. A verified renewal refreshes each allowance once for the new paid period. Cancel in Discord to stop the next renewal; access and remaining allowance continue through the current paid period. Unused units do not roll over. There are no overage charges, add-on purchases, free trial, or free AI credits.</p><p>Chat attempts may include text or a screenshot for understanding. Advanced reasoning includes its full output budget. A dispatched timeout, refusal, or unknown provider usage may use an attempt. Web search is one request with at most one search call and its answer; it does not also use a chat attempt. Image generation makes a new image; plans do not include image edits, variants, file uploads, or image hosting.</p><p>Profiles and code-based game, party, and team tools stay free. Premium AI is subject to provider availability and Discord service availability.</p><p class="fine-print">Prices are monthly in USD per server, before any applicable taxes. Discord can show localized prices at checkout.</p>'
  : '<p>Basic begins paid AI access with chat. Plus and Premium include finite allowances for advanced reasoning, web search, and image generation as shown. These are fixed per-period allowances, not unlimited access. When an allowance is exhausted, that AI feature pauses until another eligible period or a supported purchase is available. There are no overage charges or automatic refills.</p><p>A chat attempt counts at most 8,000 input tokens, including instructions, history, and images, and at most 500 total output tokens. Advanced reasoning uses the same 8,000-input-token ceiling and up to 2,000 total output tokens, including hidden reasoning. A dispatched timeout or refusal may use an attempt; validation failures before dispatch restore the AI unit.</p><p>Web search is one explicit request with at most one native search tool call; it includes the summary and does not also consume ordinary chat. An image allowance is one fixed 1024 × 1024 medium-quality JPEG attempt with a 2,000-byte prompt cap. No image editing, variants, file uploads, or indefinite image hosting are included.</p><p>Profiles and code-only game, party, and team tools are free and do not consume paid AI allowances.</p><p class="fine-print">These proposed prices and finite allowances are previews only. Taxes may be collected separately.</p>';
const planSupportCard = live
  ? `<article class="support-card"><h2>Manage your AI subscription</h2><p>Open User Settings → Subscriptions in Discord to view or cancel a Premium App subscription. Canceling stops the next renewal; access and remaining allowance continue until the current paid period ends.</p><a class="button button-secondary" href="${storeUrl}" rel="noopener noreferrer">View plans on Discord ↗</a></article>`
  : '';

const pages = {
  '/': {
    title: 'Good company. Better conversations.', description: config.brand.description,
    body: `<section class="hero"><div class="shell hero-grid"><div><div class="eyebrow">A little more life in the chat</div><h1>Good company.<br><em>Better conversations.</em></h1><p class="hero-copy">${homeAiCopy}</p><div class="hero-actions"><a class="button button-primary" href="${config.inviteUrl ? escapeHtml(config.inviteUrl) : '/support/'}"${config.inviteUrl ? ' rel="noopener noreferrer"' : ''}>${config.inviteUrl ? 'Add to a server ↗' : 'Get help'}</a><a class="button button-secondary" href="/plans/">Explore plans</a></div>${config.inviteUrl ? '' : '<p class="hero-foot">Public installation is being prepared.</p>'}</div><div class="chat-stage" aria-label="Illustrative example conversation"><div class="stage-head"><span>Community lounge</span><span class="online">In the conversation</span></div><div class="chat-line"><div class="avatar" aria-hidden="true">J</div><div class="bubble"><small>Jules</small>We have three people online and no idea what to play. Any ideas?</div></div><div class="chat-line"><div class="avatar bot" aria-hidden="true">CA</div><div class="bubble bot"><small>${escapeHtml(config.brand.name)}</small>Browse the game catalog for something everyone might like—or start a party in the channel and I can help put teams together.</div></div><div class="chat-line"><div class="avatar" aria-hidden="true">M</div><div class="bubble"><small>Micah</small>Could you split us into two balanced teams after?</div></div><div class="chat-line"><div class="avatar bot" aria-hidden="true">CA</div><div class="bubble bot"><small>${escapeHtml(config.brand.name)}</small>Sure. Join the channel party with your preferred roles and self-rated skill, then ask for two teams.</div></div><p class="chat-meta">Illustrative example · not a real conversation or testimonial</p><span class="stage-tag">Made for the group</span></div></div></section><section class="section section-white"><div class="shell"><div class="section-head"><h2>A helpful presence,<br>right where you gather.</h2><p>Easy to bring into the conversation, useful when the group needs a spark, and designed to keep member choices in their hands.</p></div><div class="feature-grid"><article class="feature-card"><span class="feature-number">01</span><h3>${live ? 'Optional AI plans' : 'Premium AI chat'}</h3><p>${live ? 'Ask questions, discuss screenshots, search the web, and generate images on plans with finite shared allowances. Voice features, image editing, variants, file uploads, and image hosting are not included.' : 'When AI plans launch, mention the assistant or use a private chat. Recent channel context can help with a reply when Discord permissions allow it. Attach a screenshot when you want to ask about an image.'}</p></article><article class="feature-card"><span class="feature-number">02</span><h3>Make game night easier</h3><p>Browse the game catalog, build a channel party, and split a roster into teams using self-reported skill and role preferences.</p></article><article class="feature-card"><span class="feature-number">03</span><h3>Set your own profile</h3><p>Members choose which game and preference details to save. Profile fields are private by default, with sharing left to the member.</p></article></div></div></section><section class="section"><div class="shell"><div class="callout"><div><div class="eyebrow">Free to use together</div><h2>Profiles and game night,<br>free to keep.</h2><p>Profiles and code-only game, party, and team tools stay free as an ongoing tier. There is no trial or free AI credit.</p><div class="hero-actions"><a class="button button-primary" href="/plans/">${live ? 'View monthly plans' : 'See the AI plan preview'}</a></div></div><div class="callout-aside">${homePlanAside}</div></div></div></section>`
  },
  '/plans/': {
    title: live ? 'Monthly AI plans' : 'AI plans preview', description: live ? 'Monthly Discord guild subscriptions with shared finite AI allowances. Profiles and code-only game-night tools stay free.' : 'Preview proposed finite AI allowances. Profiles and code-only game-night tools stay free.',
    body: `<section class="page-hero"><div class="shell"><div class="eyebrow">${live ? 'Monthly plans on Discord' : 'A clear preview'}</div><h1>Free for the group.<br>${live ? 'AI when you need it.' : 'AI plans, when ready.'}</h1><p>${plansIntro}</p></div></section><section class="content"><div class="shell">${plansNotice}<div class="plan-grid">${planCards}</div><div class="prose"><h2>What each plan includes</h2>${plansTerms}</div></div></section>`
  },
  '/privacy/': {
    title: 'Privacy policy', description: 'How the Discord application handles information.',
    body: `<section class="page-hero"><div class="shell"><div class="eyebrow">Privacy, plainly stated</div><h1>Privacy policy</h1><p>This page explains how the Discord service handles information. A separate note below covers this static website.</p></div></section><section class="content"><div class="shell"><aside class="notice"><strong>Static website disclosure.</strong> This site is a static Cloudflare Pages publication. It has no sign-in, forms, analytics, advertising pixels, custom tracking, or site cookies. Cloudflare may process technical request metadata, such as network address, browser information, and request time, to deliver and protect the site under its own service terms. This hosting disclosure is separate from the Discord Service policy below.</aside><article class="prose" aria-label="Application Privacy Policy" data-source-sha256="${createHash('sha256').update(privacyMd).digest('hex')}">${markdownBody(privacyMd)}</article></div></section>`
  },
  '/terms/': {
    title: 'Terms of service', description: 'Terms governing use of the Discord application.',
    body: `<section class="page-hero"><div class="shell"><div class="eyebrow">The terms</div><h1>Terms of service</h1><p>These are the terms for using the Discord application.</p></div></section><section class="content"><div class="shell"><article class="prose" aria-label="Terms of Service" data-source-sha256="${createHash('sha256').update(termsMd).digest('hex')}">${markdownBody(termsMd)}</article></div></section>`
  },
  '/support/': {
    title: 'Support', description: 'Get help and manage your saved data in the Discord app.',
    body: `<section class="page-hero"><div class="shell"><div class="eyebrow">Help when you need it</div><h1>Support and member controls</h1><p>Use the private controls inside the app for personal data requests. Public support issues are available for questions that cannot be resolved there.</p></div></section><section class="content"><div class="shell"><div class="support-grid">${planSupportCard}<article class="support-card"><h2>Inspect, export, or delete profile data</h2><p>In Discord, run <span class="command">/profile</span>, open <strong>Privacy &amp; data</strong>, then choose <strong>View saved data</strong>, <strong>Export settings</strong>, <strong>Make all private</strong>, or <strong>Delete my data</strong>. These controls manage saved profile settings for your account in that server.</p><p>The profile export is a private JSON file with member-entered profile settings only. It excludes chat, Steam, legacy stores, and provider logs. Confirm deletion in the private panel; it removes saved profile data and attributable older profile records from live storage. If deletion fails, the app should report that instead of claiming it succeeded.</p></article><article class="support-card"><h2>Clear other saved data</h2><p>Use <span class="command">/reset</span> to clear your private chat. A channel administrator can use <span class="command">/reset channel:true</span> to clear that channel’s shared conversation.</p><p>Removing your saved Steam reference uses <span class="command">/steam unlink</span>; leaving the current roster uses <span class="command">/party leave</span>. These are separate controls from the profile export.</p></article><article class="support-card"><h2>Ask for help</h2><p>For issues you cannot resolve in the app, open a support issue. Public issues can be read by others.</p><a class="button button-secondary" href="${escapeHtml(config.supportUrl)}" rel="noopener noreferrer">Open support issues ↗</a></article><article class="support-card"><h2>Keep private details private</h2><p>Do not include passwords, Discord tokens, API keys, private message content, or other sensitive information in a public issue. The app does not ask for your Discord password.</p><a href="/privacy/">Read the privacy policy</a></article></div><p class="fine-print">These controls do not remove your original Discord messages or information Discord or an AI provider retains under its own policy.</p></div></section>`
  },
  '/404.html': {
    title: 'Page not found', description: 'That page could not be found.',
    body: `<section class="shell not-found"><div class="eyebrow">404 · Wrong turn</div><h1>That page isn’t here.</h1><p>The link may have changed, or the page may have moved. Head back to the community assistant home page.</p><a class="button button-primary" href="/">Back to home</a></section>`
  }
};

function header(pathname) {
  const links = [['Home', '/'], ['Plans', '/plans/'], ['Privacy', '/privacy/'], ['Terms', '/terms/'], ['Support', '/support/']];
  const invite = config.inviteUrl
    ? `<a class="nav-invite" href="${escapeHtml(config.inviteUrl)}" rel="noopener noreferrer">Invite ↗</a>`
    : '<a class="nav-invite" href="/support/">Get help</a>';
  return `<a class="skip-link" href="#main">Skip to content</a><header class="topbar"><div class="shell nav"><a class="brand" href="/" aria-label="${escapeHtml(config.brand.name)} home"><span class="mark" aria-hidden="true">C</span><span>${escapeHtml(config.brand.shortName)}</span></a><nav class="nav-links" aria-label="Main navigation">${links.map(([label, href]) => `<a href="${href}"${href === pathname ? ' aria-current="page"' : ''}>${label}</a>`).join('')}</nav>${invite}</div></header>`;
}

function footer() {
  return `<footer class="footer"><div class="shell"><div class="footer-top"><div><a class="brand" href="/"><span class="mark" aria-hidden="true">C</span><span>${escapeHtml(config.brand.shortName)}</span></a><p>${escapeHtml(config.brand.tagline)}</p></div><nav class="footer-links" aria-label="Footer navigation"><a href="/plans/">Plans</a><a href="/privacy/">Privacy</a><a href="/terms/">Terms</a><a href="/support/">Support</a><a href="${escapeHtml(config.sourceUrl)}" rel="noopener noreferrer">Source</a></nav></div><div class="footer-bottom"><span>© ${escapeHtml(config.brand.name)}</span><span>Independent community software. Third-party names belong to their respective owners.</span></div></div></footer>`;
}

await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });
await cp(path.join(site, 'src/styles.css'), path.join(out, 'styles.css'));
await cp(path.join(site, 'src/favicon.svg'), path.join(out, 'favicon.svg'));
await cp(path.join(site, '_headers'), path.join(out, '_headers'));
await cp(path.join(site, 'robots.txt'), path.join(out, 'robots.txt'));

const publicRoutes = ['/', '/plans/', '/privacy/', '/terms/', '/support/'];
const sitemap = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${publicRoutes.map((route) => `  <url><loc>${config.baseUrl}${route}</loc></url>`).join('\n')}\n</urlset>\n`;
await writeFile(path.join(out, 'sitemap.xml'), sitemap, 'utf8');

for (const [route, page] of Object.entries(pages)) {
  const canonical = route === '/404.html' ? '' : `<link rel="canonical" href="${config.baseUrl}${route}">`;
  const pageBody = page.body.replaceAll('<main ', '<section ').replaceAll('</main>', '</section>');
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex,nofollow"><meta name="description" content="${escapeHtml(page.description)}"><meta name="theme-color" content="#f7f7f2"><title>${escapeHtml(page.title)} · ${escapeHtml(config.brand.name)}</title>${canonical}<link rel="icon" type="image/svg+xml" href="/favicon.svg"><link rel="stylesheet" href="/styles.css"></head><body>${header(route)}<main id="main">${pageBody}</main>${footer()}</body></html>`;
  const target = route === '/' ? path.join(out, 'index.html') : route === '/404.html' ? path.join(out, '404.html') : path.join(out, route.slice(1), 'index.html');
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, html, 'utf8');
}

console.log(`Built ${Object.keys(pages).length} routes in ${out}`);
