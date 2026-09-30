import { createHash } from 'node:crypto';
import { readFile, readdir, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const out = path.join(site, 'dist');
const repo = path.resolve(site, '..');
const read = (file) => readFile(file, 'utf8');
const checks = [];
const check = (condition, message) => { if (!condition) throw new Error(`FAIL: ${message}`); checks.push(message); };
const files = [];
async function walk(dir) {
  for (const item of await readdir(dir, { withFileTypes: true })) {
    const target = path.join(dir, item.name);
    if (item.isDirectory()) await walk(target); else files.push(target);
  }
}
await walk(out);
const htmlFiles = files.filter((file) => file.endsWith('.html'));
check(htmlFiles.length === 6, 'all six static routes are built');
for (const route of ['index.html', 'plans/index.html', 'privacy/index.html', 'terms/index.html', 'support/index.html', '404.html']) {
  check(files.includes(path.join(out, route)), `${route} exists`);
}
const html = await Promise.all(htmlFiles.map(read));
const allHtml = html.join('\n');
for (const route of ['/plans/', '/privacy/', '/terms/', '/support/']) {
  check((allHtml.match(new RegExp(`<a href="${route.replaceAll('/', '\\/')}"`, 'g')) ?? []).length >= 2, `${route} is linked internally`);
}
const configuredSaleStatus = JSON.parse(await read(path.join(site, 'site.config.json'))).saleStatus;
const saleStatus = process.env.NODE_ENV === 'test' ? (process.env.SITE_SALE_STATUS || configuredSaleStatus) : configuredSaleStatus;
check(saleStatus === 'live' ? (allHtml.match(/class="button button-primary plan-purchase"/g) ?? []).length === 3 : !/<a[^>]*>[^<]*(buy now|subscribe now|checkout|purchase)[^<]*<\/a>/i.test(allHtml), saleStatus === 'live' ? 'all live plan cards have purchase links' : 'preview has no purchase or checkout call to action');
check(!/<script\b|google-analytics|googletagmanager|facebook\.net|segment\.com|hotjar/i.test(allHtml), 'pages have no JavaScript, analytics, or tracker');
check(!/href="#"/.test(allHtml), 'no dead placeholder link is present');
check(saleStatus === 'live' || JSON.parse(await read(path.join(site, 'site.config.json'))).inviteUrl ? /Add to a server ↗/.test(allHtml) : /Public installation is being prepared\./.test(allHtml), 'installation link matches the configured availability');
check(/illustrative example/i.test(allHtml) && /not a real conversation or testimonial/i.test(allHtml), 'sample conversation is labeled illustrative');
check(!/Steam libraries/.test(allHtml), 'site does not promise Steam library integration');
check(allHtml.includes('Export settings') && allHtml.includes('Delete my data') && allHtml.includes('/profile'), 'support describes the private profile data controls');
check(allHtml.includes('Cloudflare may process technical request metadata') && allHtml.includes('separate from the Discord Service policy'), 'website hosting disclosure is distinct');

const config = JSON.parse(await read(path.join(site, 'site.config.json')));
const configJson = JSON.stringify(config);
const baseUrl = new URL(config.baseUrl);
check(baseUrl.protocol === 'https:' && baseUrl.pathname === '/' && !baseUrl.username && !baseUrl.password, 'canonical base is a clean HTTPS origin');
check(!allHtml.includes(configJson), 'private build configuration is not copied into public output');
for (const [route, page] of [['/', html[htmlFiles.indexOf(path.join(out, 'index.html'))]], ['/plans/', await read(path.join(out, 'plans/index.html'))], ['/privacy/', await read(path.join(out, 'privacy/index.html'))], ['/terms/', await read(path.join(out, 'terms/index.html'))], ['/support/', await read(path.join(out, 'support/index.html'))]]) {
  check(page.includes(`rel="canonical" href="${config.baseUrl}${route}"`), `${route} has its canonical URL`);
  const navMatches = page.match(/aria-current="page"/g) ?? [];
  check(navMatches.length === 1, `${route} marks exactly one current navigation link`);
}
check(allHtml.includes(config.brand.name) && allHtml.includes(config.brand.shortName), 'central brand values appear in the static site');
const sitemap = await read(path.join(out, 'sitemap.xml'));
check(sitemap.includes(`${config.baseUrl}/plans/`) && sitemap.includes(`${config.baseUrl}/support/`), 'sitemap contains canonical public routes');

const actualPrivacy = await read(path.join(repo, 'PRIVACY.md'));
const actualTerms = await read(path.join(repo, 'TERMS.md'));
const privacyPage = await read(path.join(out, 'privacy/index.html'));
const termsPage = await read(path.join(out, 'terms/index.html'));
const hash = (value) => createHash('sha256').update(value).digest('hex');
check(privacyPage.includes(`data-source-sha256="${hash(actualPrivacy)}"`), 'privacy page is built from the exact repository policy file');
check(termsPage.includes(`data-source-sha256="${hash(actualTerms)}"`), 'terms page is built from the exact repository terms file');
for (const required of ['September 29, 2026', 'Members can use the private profile/privacy controls', 'do not include passwords, tokens']) check(privacyPage.includes(required), `privacy page includes source policy statement: ${required}`);
for (const required of ['September 29, 2026', 'Paid features', 'unbounded usage charges']) check(termsPage.toLowerCase().includes(required.toLowerCase()), `terms page includes source terms statement: ${required}`);
check(privacyPage.includes(`href="${config.supportUrl}"`) && termsPage.includes(`href="${config.supportUrl}"`), 'raw support addresses in policy contact sections are clickable');

const pricing = await read(path.join(repo, config.pricing.source));
const plansPage = await read(path.join(out, 'plans/index.html'));
for (const value of ['$1.99', '$4.99', '$9.99', '400', '500', '1000', '50', '100', '10', '20', '3']) check(plansPage.includes(value), `plan page includes price/allowance ${value}`);
check(!plansPage.includes('Server operations') && !plansPage.includes('Saved data') && !plansPage.includes('Optional add-ons') && !plansPage.includes('Extra chat') && !plansPage.includes('Storage boost'), 'public plans omit core counts and unsupported add-on offers');
check(pricing.includes('## Included allowances'), 'published allowances are derived from the reviewed plan table');
if (saleStatus === 'live') {
  for (const [sku, product] of Object.entries({ '1554920142532513832': 'Basic', '1554920641088593990': 'Plus', '1554920977488551936': 'Premium' })) {
    check(plansPage.includes(`https://discord.com/application-directory/1365724363722068120/store/${sku}`), `${product} links to its reviewed Discord SKU`);
  }
  for (const phrase of ['shared across this server', 'authenticated Discord billing period', 'refreshes each allowance once', 'access and remaining allowance continue through the current paid period', 'does not roll over', 'no overage charges', 'Cancel any time in Discord subscription settings', 'free trial', 'image edits', 'image hosting', 'unknown provider usage may use an attempt']) check(plansPage.toLowerCase().includes(phrase.toLowerCase()), `live plans disclose ${phrase}`);
  const supportPage = await read(path.join(out, 'support/index.html'));
  check(supportPage.includes('Manage your AI subscription') && supportPage.includes('Subscriptions in Discord'), 'live support explains how to manage Discord subscriptions');
  check(!/not available for purchase yet|no purchase or charge can occur/i.test(plansPage), 'live plans do not carry inactive-checkout messaging');
} else {
  check(plansPage.toLowerCase().includes('no free ai credit'), 'preview explains there are no free AI credits');
  for (const value of ['8,000 input tokens', '500 total output tokens', '2,000 total output tokens', 'including hidden reasoning']) check(plansPage.toLowerCase().includes(value.toLowerCase()), `preview explains ${value}`);
  for (const value of ['not available for purchase yet', 'no purchase or charge can occur']) check(plansPage.toLowerCase().includes(value), `preview clearly says ${value}`);
  check(!plansPage.includes('application-directory/'), 'preview has no active SKU checkout links');
}

const routeForHref = (href) => {
  const clean = href.split(/[?#]/, 1)[0];
  if (!clean.startsWith('/')) return null;
  if (clean === '/') return path.join(out, 'index.html');
  if (clean.endsWith('/')) return path.join(out, clean.slice(1), 'index.html');
  return path.join(out, clean.slice(1));
};
for (const page of html) {
  for (const [, href] of page.matchAll(/href="([^"]+)"/g)) {
    const target = routeForHref(href);
    if (target) {
      try { await stat(target); } catch { throw new Error(`FAIL: internal link does not resolve: ${href}`); }
    }
  }
}
check(true, 'all generated internal links resolve to output files');
check(await stat(path.join(out, 'favicon.svg')).then(() => true), 'original site favicon is emitted');

const headers = await read(path.join(out, '_headers'));
for (const header of ['Content-Security-Policy:', "script-src 'none'", 'X-Content-Type-Options:', 'Referrer-Policy:', 'Permissions-Policy:']) check(headers.includes(header), `${header} is configured`);
check(html.every((page) => page.includes('name="robots" content="noindex,nofollow"')), 'pilot pages are marked noindex');
console.log(`PASS: ${checks.length} checks across ${htmlFiles.length} routes`);
