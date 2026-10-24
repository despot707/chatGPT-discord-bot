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

const planBenefits = {
  Basic: ['For a server that wants AI chat', 'Chat for questions and conversation.'],
  Plus: ['For a server that wants more AI tools', 'Adds advanced reasoning, web search, and image creation.'],
  Premium: ['For a server with frequent AI use', 'More shared monthly chat, reasoning, search, and image allowances.'],
};
const planCards = `<article class="plan free-tier"><div class="plan-label">Best for getting your server playing</div><h2>Free</h2><div class="price">$0<small> / ongoing community tools</small></div><p>Member profiles, player discovery, parties, and balanced teams. No AI credit or trial.</p><div class="allowances"><div class="allowance"><span>Game night</span><span>Included</span></div><div class="allowance"><span>Member profiles</span><span>Included</span></div><div class="allowance"><span>AI features</span><span>Optional</span></div></div><a class="button button-secondary plan-purchase" href="/getting-started/">Start with free tools</a></article>${planRows.map((row, index) => {
  const [bestFor, benefit] = planBenefits[row[0]];
  return `<article class="plan${index === 1 ? ' featured' : ''}"><div class="plan-label">${escapeHtml(bestFor)}</div><h2>${escapeHtml(row[0])}</h2><div class="price">${escapeHtml(row[1])}<small> / server / month</small></div><p>${escapeHtml(benefit)} ${live ? 'Shared by this server each billing period.' : 'Proposed finite AI allowance per eligible period.'}</p><div class="allowances">${allowanceRows(planHeaders, row)}</div>${live ? `<a class="button button-primary plan-purchase" href="${productUrl(row[0].toLowerCase())}" rel="noopener noreferrer">View ${escapeHtml(row[0])} on Discord ↗</a>` : ''}</article>`;
}).join('')}`;

const plansIntro = live
  ? 'Pick a plan by the AI tools your server will use. Player discovery, member profiles, parties, and balanced teams stay free either way. Each monthly AI allowance is shared by members in the server.'
  : 'Choose by the AI tools your server expects to use. Player discovery, profiles, parties, and balanced teams stay free. There is no trial or free AI credit; these proposed finite allowances are priced per server in USD.';
const plansNotice = live
  ? '<div class="notice" role="status"><strong>Subscribe through Discord.</strong> Each monthly allowance is shared across the server. Cancel any time in Discord subscription settings; access continues through the current paid period. Unused allowance does not roll over, and there are no overage charges or mid-period top-ups.</div>'
  : '<div class="notice" role="status"><strong>Premium AI plans are not available for purchase yet.</strong> Free gaming tools are ongoing, not a trial, and there is no free AI credit. Prices and allowances are previews. No purchase or charge can occur in this preview.</div>';
const plansTerms = live
  ? '<p>Allowances are finite and shared by members of the subscribed server for each authenticated Discord billing period. Discord renews subscriptions automatically unless cancelled. A verified renewal refreshes each allowance once for the new paid period. Cancel in Discord to stop the next renewal; access and remaining allowance continue through the current paid period. Unused units do not roll over. There are no overage charges, add-on purchases, free trial, or free AI credits.</p><p>Chat attempts may include text or a screenshot for understanding. Advanced reasoning includes its full output budget. A dispatched timeout, refusal, or unknown provider usage may use an attempt. Web search is one request with at most one search call and its answer; it does not also use a chat attempt. Image generation makes a new image; plans do not include image edits, variants, file uploads, or image hosting.</p><p>Profiles and code-based game, party, and team tools stay free. Premium AI is subject to provider availability and Discord service availability.</p><p class="fine-print">Prices are monthly in USD per server, before any applicable taxes. Discord can show localized prices at checkout.</p>'
  : '<p>Basic begins paid AI access with chat. Plus and Premium include finite allowances for advanced reasoning, web search, and image generation as shown. These are fixed per-period allowances, not unlimited access. When an allowance is exhausted, that AI feature pauses until another eligible period or a supported purchase is available. There are no overage charges or automatic refills.</p><p>A chat attempt counts at most 8,000 input tokens, including instructions, history, and images, and at most 500 total output tokens. Advanced reasoning uses the same 8,000-input-token ceiling and up to 2,000 total output tokens, including hidden reasoning. A dispatched timeout or refusal may use an attempt; validation failures before dispatch restore the AI unit.</p><p>Web search is one explicit request with at most one native search tool call; it includes the summary and does not also consume ordinary chat. An image allowance is one fixed 1024 × 1024 medium-quality JPEG attempt with a 2,000-byte prompt cap. No image editing, variants, file uploads, or indefinite image hosting are included.</p><p>Profiles and code-only game, party, and team tools are free and do not consume paid AI allowances.</p><p class="fine-print">These proposed prices and finite allowances are previews only. Taxes may be collected separately.</p>';
const planSupportCard = live
  ? `<article class="support-card"><h2>Manage your AI subscription</h2><p>Open User Settings → Subscriptions in Discord to view or cancel a Premium App subscription. Canceling stops the next renewal; access and remaining allowance continue until the current paid period ends.</p><a class="button button-secondary" href="${storeUrl}" rel="noopener noreferrer">View plans on Discord ↗</a></article>`
  : '';
const homepageDescription = live
  ? 'Free Discord tools for game nights: find players who share a game, organize a party, and balance teams. Optional server AI plans are available.'
  : 'Free Discord tools for game nights: find players who share a game, organize a party, and balance teams. Optional server AI plans are in preview.';

const pages = {
  '/': {
    title: 'Find your next player. Build balanced teams.', description: homepageDescription,
    body: `<section class="hero gaming-hero"><div class="shell hero-grid"><div><div class="eyebrow">A better game night starts here</div><h1>Find your people.<br><em>Get in the game.</em></h1><p class="hero-copy">Find server members who play your games, gather a party, and build balanced teams. Free gaming tools for your Discord community. ${live ? 'Optional AI plans when your server wants them.' : 'Optional server AI plans are in preview.'}</p><div class="hero-actions"><a class="button button-primary" href="${config.inviteUrl ? escapeHtml(config.inviteUrl) : '/support/'}"${config.inviteUrl ? ' rel="noopener noreferrer"' : ''}>${config.inviteUrl ? 'Add to Discord ↗' : 'Get help'}</a><a class="button button-secondary" href="/getting-started/">See how it works</a></div><p class="hero-foot">No AI needed for profiles, player discovery, parties, or teams.</p>${config.inviteUrl ? "" : "<p class=\"hero-foot\">Public installation is being prepared.</p>"}</div><div class="game-demo" aria-label="Illustrative interface showing a game search and member-shared results"><div class="demo-top"><span class="demo-guild"><span class="guild-mark">S</span><span><strong>SERVER MEMBERS</strong><small>game night</small></span></span><span class="demo-status">PRIVATE SEARCH</span></div><div class="demo-channel"># <strong>find players</strong> <span>PRIVATE REPLY</span></div><div class="demo-command"><span class="slash">/</span>games <span class="command-arg">game: Overwatch 2</span><span class="enter">↵</span></div><div class="demo-result"><div class="result-heading"><span class="result-icon">◎</span><span><strong>Players for Overwatch 2</strong><small>Only server-shared games appear</small></span></div><div class="member-row"><span class="member-avatar avatar-one">M</span><span>mira<small>Flex · Both</small></span></div><div class="member-row"><span class="member-avatar avatar-two">K</span><span>kai<small>Support · Casual</small></span></div><div class="member-row"><span class="member-avatar avatar-three">J</span><span>jules<small>Tank · Competitive</small></span></div></div><div class="demo-bottom"><span><i class="tiny-check">✓</i> Member-shared games</span><span><i class="tiny-check">✓</i> Balanced teams</span></div><p class="demo-caption">Illustrative interface · sample members and results</p></div></div></section><section class="section section-white"><div class="shell"><div class="section-head"><h2>From “who’s on?”<br>to ready to play.</h2><p>Give your group a simple way to find teammates and get a match going, right in the server you already use.</p></div><div class="feature-grid"><article class="feature-card"><span class="feature-number">01</span><h3>Find players</h3><p>Search a game to see server members who chose to share that game in their profile.</p><a href="/getting-started/">See how sharing works →</a></article><article class="feature-card"><span class="feature-number">02</span><h3>Bring the party together</h3><p>Join the channel party with your role and self-rated skill, then see who’s in before splitting into teams.</p><a href="/getting-started/#party">Build a party →</a></article><article class="feature-card"><span class="feature-number">03</span><h3>Balance the teams</h3><p>Turn the party roster into two to four teams using the ratings people chose for themselves.</p><a href="/getting-started/#teams">Make teams →</a></article></div></div></section><section class="section"><div class="shell"><div class="callout"><div><div class="eyebrow">Free for your whole community</div><h2>Game night stays free.</h2><p>Member profiles, finding players, parties, and team balancing use code-only tools. ${live ? "Optional monthly AI subscriptions add chat, reasoning, web search, and image creation for servers that want them." : "Optional monthly AI plans are in preview; the free gaming tools are ready to use without one."}</p><div class="hero-actions"><a class="button button-primary" href="/getting-started/">Get started</a><a class="button button-secondary" href="/plans/">Compare AI plans</a></div></div><div class="callout-aside"><strong>Made for gaming servers</strong>Find your next teammate, get a party together, and start playing with less coordination overhead.</div></div></div></section>`
  },
  '/getting-started/': {
    title: 'Get started with game night', description: 'A quick guide to finding players who share a game, joining a channel party, and making fair teams with Sidecord in Discord.',
    body: `<section class="page-hero guide-hero"><div class="shell"><div class="eyebrow">Your first game night</div><h1>Four quick steps to<br>your next match.</h1><p>Sidecord helps your Discord server find players, gather a party, and build balanced teams. The free gaming tools work without an AI plan.</p><div class="hero-actions"><a class="button button-primary" href="${config.inviteUrl ? escapeHtml(config.inviteUrl) : '/support/'}"${config.inviteUrl ? ' rel="noopener noreferrer"' : ''}>${config.inviteUrl ? 'Add Sidecord to Discord ↗' : 'Get help'}</a><a class="button button-secondary" href="/plans/">See optional AI plans</a></div></div></section><section class="content guide-content"><div class="shell"><div class="guide-grid"><article class="guide-step" id="share"><div class="step-index">01</div><div><div class="step-kicker">Everyone who wants to be found</div><h2>Share the games you play</h2><p>In your server, run <span class="command">/profile</span> → <strong>Games</strong> → <strong>Search games</strong> and select a catalog result. Choose <strong>Edit game</strong>. In the <strong>Who can see this game?</strong> field, select <strong>This server</strong>, then submit the form to save. New games start private; you can change sharing later.</p><div class="step-note"><strong>Privacy is your choice:</strong> only titles you share with this server appear in player searches.</div></div></article><article class="guide-step" id="players"><div class="step-index">02</div><div><div class="step-kicker">Find your people</div><h2>See who plays your game</h2><p>In the channel where you want to find players, run <span class="command">/games</span> and select a title in the optional game field. You can also open <strong>Find players</strong> to search the catalog. Results include current server members who shared that game with this server.</p><div class="step-note"><strong>Member controlled:</strong> only games a member chose to share appear in results.</div></div></article><article class="guide-step" id="party"><div class="step-index">03</div><div><div class="step-kicker">Get the group together</div><h2>Join your channel party</h2><p>In the text channel where you’re coordinating, run <span class="command">/games</span> → <strong>Party &amp; teams</strong> → <strong>Join party</strong>. Choose a role and rate your skill from 1 to 10. Use <strong>Show party</strong> to see who’s ready.</p><div class="step-note"><strong>Keep it current:</strong> leave the party when you’re done so the roster stays useful.</div></div></article><article class="guide-step" id="teams"><div class="step-index">04</div><div><div class="step-kicker">Ready, set, play</div><h2>Make balanced teams</h2><p>From <span class="command">/games</span> → <strong>Party &amp; teams</strong>, choose <strong>Make teams</strong> and pick 2–4 teams. Sidecord uses the party’s self-reported skill ratings to balance teams.</p><div class="step-note"><strong>Member-provided ratings guide the balance.</strong> Roles are preferences, and even ratings do not guarantee an equal match.</div></div></article></div><aside class="guide-aside"><span class="eyebrow">Optional server AI</span><h2>Want more help in chat?</h2><p>${live ? "Paid server plans add finite monthly allowances for AI chat, reasoning, web search, and image generation. Game night, profiles, and player discovery remain free." : "Paid server AI plans are in preview, with finite monthly allowances. Game night, profiles, and player discovery remain free."}</p><a href="/plans/" class="text-link">Compare plans and allowances →</a></aside></div></section>`
  },
  '/plans/': {
    title: live ? 'Optional AI plans for your server' : 'Optional AI plans preview', description: live ? 'Compare Basic, Plus, and Premium server AI subscriptions. Free gaming tools, member profiles, player discovery, parties, and teams remain available.' : 'Preview proposed server AI allowances. Free gaming tools, member profiles, player discovery, parties, and teams remain available.',
    body: `<section class="page-hero"><div class="shell"><div class="eyebrow">${live ? 'Optional monthly server plans' : 'A clear preview'}</div><h1>Free game night.<br><em>AI if you want it.</em></h1><p>${plansIntro}</p><div class="hero-actions"><a href="/getting-started/" class="button button-secondary">Start with free tools</a></div></div></section><section class="content"><div class="shell">${plansNotice}<div class="plan-grid">${planCards}</div><div class="prose"><h2>Allowance details</h2>${plansTerms}</div></div></section>`
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
    body: `<section class="page-hero"><div class="shell"><div class="eyebrow">Help when you need it</div><h1>Support and member controls</h1><p>Use the private controls inside the app for personal data requests. Public support issues are available for questions that cannot be resolved there.</p></div></section><section class="content"><div class="shell"><div class="support-grid">${planSupportCard}<article class="support-card"><h2>Inspect, export, or delete profile data</h2><p>In Discord, run <span class="command">/profile</span>, open <strong>Privacy &amp; data</strong>, then choose <strong>View saved data</strong>, <strong>Export settings</strong>, <strong>Make all private</strong>, or <strong>Delete my data</strong>. These controls manage saved profile settings for your account in that server.</p><p>The profile export is a private JSON file with member-entered profile settings only. It excludes chat, Steam, legacy stores, and provider logs. Confirm deletion in the private panel; it removes saved profile data and attributable older profile records from live storage. If deletion fails, the app should report that instead of claiming it succeeded.</p></article><article class="support-card"><h2>Clear other saved data</h2><p>In Discord, open <span class="command">/settings</span> → <strong>Chat &amp; data</strong>. Choose <strong>Clear private chat</strong> to remove your older private conversation here. Members with <strong>Manage Channels</strong> permission or configured bot administrators can also choose <strong>Clear shared chat</strong> for this channel.</p><p>To leave the current roster, open <span class="command">/games</span> → <strong>Party &amp; teams</strong> → <strong>Leave party</strong>. If you already have a saved Steam link, remove it from <span class="command">/games</span> → <strong>Steam</strong> → <strong>Unlink Steam</strong>.</p></article><article class="support-card"><h2>Ask for help</h2><p>For issues you cannot resolve in the app, open a support issue. Public issues can be read by others.</p><a class="button button-secondary" href="${escapeHtml(config.supportUrl)}" rel="noopener noreferrer">Open support issues ↗</a></article><article class="support-card"><h2>Keep private details private</h2><p>Do not include passwords, Discord tokens, API keys, private message content, or other sensitive information in a public issue. The app does not ask for your Discord password.</p><a href="/privacy/">Read the privacy policy</a></article></div><p class="fine-print">These controls do not remove your original Discord messages or information Discord or an AI provider retains under its own policy.</p></div></section>`
  },
  '/404.html': {
    title: 'Page not found', description: 'That page could not be found.',
    body: `<section class="shell not-found"><div class="eyebrow">404 · Wrong turn</div><h1>That page isn’t here.</h1><p>The link may have changed, or the page may have moved. Head back to the community assistant home page.</p><a class="button button-primary" href="/">Back to home</a></section>`
  }
};

function header(pathname) {
  const links = [['Home', '/'], ['How it works', '/getting-started/'], ['AI plans', '/plans/'], ['Privacy', '/privacy/'], ['Terms', '/terms/'], ['Support', '/support/']];
  const invite = config.inviteUrl
    ? `<a class="nav-invite" href="${escapeHtml(config.inviteUrl)}" rel="noopener noreferrer">Invite ↗</a>`
    : '<a class="nav-invite" href="/support/">Get help</a>';
  return `<a class="skip-link" href="#main">Skip to content</a><header class="topbar"><div class="shell nav"><a class="brand" href="/" aria-label="${escapeHtml(config.brand.name)} home"><span class="mark" aria-hidden="true">S</span><span>${escapeHtml(config.brand.shortName)}</span></a><nav class="nav-links" aria-label="Main navigation">${links.map(([label, href]) => `<a href="${href}"${href === pathname ? ' aria-current="page"' : ''}>${label}</a>`).join('')}</nav>${invite}</div></header>`;
}

function footer() {
  return `<footer class="footer"><div class="shell"><div class="footer-top"><div><a class="brand" href="/"><span class="mark" aria-hidden="true">S</span><span>${escapeHtml(config.brand.shortName)}</span></a><p>Find players, form parties, and get a fair game going in Discord.</p></div><nav class="footer-links" aria-label="Footer navigation"><a href="/getting-started/">How it works</a><a href="/plans/">AI plans</a><a href="/privacy/">Privacy</a><a href="/terms/">Terms</a><a href="/support/">Support</a><a href="${escapeHtml(config.sourceUrl)}" rel="noopener noreferrer">Source</a></nav></div><div class="footer-bottom"><span>© ${escapeHtml(config.brand.name)}</span><span>Independent community software. Third-party names belong to their respective owners.</span></div></div></footer>`;
}

await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });
await cp(path.join(site, 'src/styles.css'), path.join(out, 'styles.css'));
await cp(path.join(site, 'src/favicon.svg'), path.join(out, 'favicon.svg'));
await cp(path.join(site, '_headers'), path.join(out, '_headers'));
await cp(path.join(site, 'src/social-card.png'), path.join(out, 'social-card.png'));
await writeFile(path.join(out, 'robots.txt'), live ? `User-agent: *\nAllow: /\nSitemap: ${config.baseUrl}/sitemap.xml\n` : 'User-agent: *\nDisallow: /\n', 'utf8');

const publicRoutes = live ? ['/', '/getting-started/', '/plans/', '/privacy/', '/terms/', '/support/'] : [];
const sitemap = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${publicRoutes.map((route) => `  <url><loc>${config.baseUrl}${route}</loc></url>`).join('\n')}\n</urlset>\n`;
await writeFile(path.join(out, 'sitemap.xml'), sitemap, 'utf8');

for (const [route, page] of Object.entries(pages)) {
  const canonical = route === '/404.html' ? '' : `<link rel="canonical" href="${config.baseUrl}${route}">`;
  const pageBody = page.body.replaceAll('<main ', '<section ').replaceAll('</main>', '</section>');
  const indexable = live && route !== '/404.html';
  const robots = indexable ? 'index,follow' : 'noindex,nofollow';
  const canonicalUrl = `${config.baseUrl}${route}`;
  const social = indexable ? `<meta property="og:type" content="website"><meta property="og:site_name" content="${escapeHtml(config.brand.name)}"><meta property="og:title" content="${escapeHtml(page.title)}"><meta property="og:description" content="${escapeHtml(page.description)}"><meta property="og:url" content="${canonicalUrl}"><meta property="og:image" content="${config.baseUrl}/social-card.png"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630"><meta property="og:image:alt" content="Sidecord helps gaming communities find players, gather parties, and build balanced teams."><meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="${escapeHtml(page.title)}"><meta name="twitter:description" content="${escapeHtml(page.description)}"><meta name="twitter:image" content="${config.baseUrl}/social-card.png">` : '';
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="${robots}"><meta name="description" content="${escapeHtml(page.description)}"><meta name="theme-color" content="#f7f7f2"><title>${escapeHtml(page.title)} · ${escapeHtml(config.brand.name)}</title>${canonical}${social}<link rel="icon" type="image/svg+xml" href="/favicon.svg"><link rel="stylesheet" href="/styles.css"></head><body>${header(route)}<main id="main">${pageBody}</main>${footer()}</body></html>`;
  const target = route === '/' ? path.join(out, 'index.html') : route === '/404.html' ? path.join(out, '404.html') : path.join(out, route.slice(1), 'index.html');
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, html, 'utf8');
}

console.log(`Built ${Object.keys(pages).length} routes in ${out}`);
