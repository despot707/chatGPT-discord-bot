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
check(!/<a[^>]*>[^<]*(buy now|subscribe now|checkout|purchase)[^<]*<\/a>/i.test(allHtml), 'no purchase or checkout call to action is present');
check(!/<script\b|google-analytics|googletagmanager|facebook\.net|segment\.com|hotjar/i.test(allHtml), 'pages have no JavaScript, analytics, or tracker');
check(!/href="#"/.test(allHtml), 'no dead placeholder link is present');
check(/Public installation is being prepared\./.test(allHtml) && /<a class="button button-primary" href="\/support\/">Get help<\/a>/.test(allHtml), 'unverified installation routes people to support with a clear notice');
check(/illustrative example/i.test(allHtml) && /not a real conversation or testimonial/i.test(allHtml), 'sample conversation is labeled illustrative');
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
for (const value of ['$0.99', '$4.99', '$9.99', '100', '500', '1000', '50', '100', '10', '20', '3']) check(plansPage.includes(value), `plan preview includes unchanged AI price/allowance ${value}`);
for (const value of ['ongoing free tier', 'no free AI credit', 'Basic begins paid AI access', '8,000 input tokens', '500 total output tokens', '2,000 total output tokens', 'including hidden reasoning', 'not available for purchase yet', 'no purchase or charge can occur', 'no overage charges or automatic refills']) check(plansPage.toLowerCase().includes(value.toLowerCase()), `plan page explains ${value}`);
check(!plansPage.includes('Server operations') && !plansPage.includes('Saved data') && !plansPage.includes('Optional add-ons') && !plansPage.includes('$1.99') && !plansPage.includes('$2.99'), 'public plans omit core counts and unsupported add-on offers');
check(pricing.includes('AI purchases are not enabled'), 'source plan status confirms AI purchases are disabled');
check(plansPage.toLowerCase().includes('not available for purchase yet') && plansPage.toLowerCase().includes('no purchase or charge can occur'), 'plan page clearly keeps AI sales disabled');

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
