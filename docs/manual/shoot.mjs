// Capture the user-manual screenshots (docs/manual/shots/*.png, git-ignored) from a running CakeCRM
// loaded with the fictional sample data. `--seed` WIPES the CRM first (clear-all) and loads the sample
// set, so point it only at a scratch or demo install — never one holding real records.
//
//   CAKECRM_PASSWORD=... node docs/manual/shoot.mjs --seed   # reset + sample data, then shoot all
//   CAKECRM_PASSWORD=... node docs/manual/shoot.mjs [name]   # re-shoot all, or only shots whose name contains [name]
//
// Then `python docs/manual/build.py` frames them and refreshes the committed webps in website/manual/shots.
// Env: CAKECRM_URL (default http://localhost:5173, the Vite dev server), CAKECRM_EMAIL (default the
// .env ADMIN_EMAIL), CAKECRM_PASSWORD (an admin's), PLAYWRIGHT_CORE (path to playwright-core if it
// isn't resolvable from here).
import fs from 'node:fs';
import path from 'node:path';
const { chromium } = await import(process.env.PLAYWRIGHT_CORE || 'playwright-core');
const HERE = path.dirname(new URL(import.meta.url).pathname);
const ROOT = path.resolve(HERE, '../..');
const BASE = process.env.CAKECRM_URL || 'http://localhost:5173';
const envFile = fs.existsSync(path.join(ROOT, '.env')) ? fs.readFileSync(path.join(ROOT, '.env'), 'utf8') : '';
const EMAIL = process.env.CAKECRM_EMAIL || envFile.match(/^ADMIN_EMAIL=(.*)$/m)?.[1];
const args = process.argv.slice(2);
const only = args.find(a => !a.startsWith('--'));
const OUT = path.join(HERE, 'shots');
fs.mkdirSync(OUT, { recursive: true });

async function api(method, url, body, token) {
  const r = await fetch(BASE + url, { method, headers: { 'content-type': 'application/json', ...(token && { authorization: `Bearer ${token}` }) }, body: body && JSON.stringify(body) });
  if (!r.ok) throw new Error(`${method} ${url}: ${r.status} ${await r.text()}`);
  return r.json();
}
const TOKEN = (await api('POST', '/api/login', { email: EMAIL, password: process.env.CAKECRM_PASSWORD })).access_token;

if (args.includes('--seed')) {
  await api('POST', '/api/crm/clear-all', { confirmation: 'clear crm' }, TOKEN);
  await api('POST', '/api/crm/load-sample-data', null, TOKEN);
  await api('POST', '/api/crm/dismiss-ai-prompt', null, TOKEN);
  // The sample set has no GTD project, nothing waiting and nothing parked for someday: add a few.
  const proj = await api('POST', '/api/crm/gtd/projects', { name: 'Launch the holiday gift box line', outcome: 'Gift boxes on sale by mid-November' }, TOKEN);
  for (const t of [{ title: 'Price the three box sizes', status: 'next_action', context: '@computer' },
                   { title: 'Order printed ribbon', status: 'next_action', context: '@calls' },
                   { title: 'Box supplier: sample boxes in the post', status: 'waiting_for' }])
    await api('POST', '/api/crm/gtd/todos', { ...t, project_id: proj.id }, TOKEN);
  await api('POST', '/api/crm/gtd/todos', { title: 'Hannah Lee: signed order form for the fundraiser', status: 'waiting_for' }, TOKEN);
  await api('POST', '/api/crm/gtd/todos', { title: 'Try a weekend farmers market stand', status: 'someday_maybe' }, TOKEN);
  console.log('seeded');
}

const browser = await chromium.launch();
async function newPage({ w = 1280, h = 800, dark = false, auth = true } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 2, colorScheme: dark ? 'dark' : 'light' });
  await ctx.addInitScript(([t, auth, dark]) => {
    if (auth) sessionStorage.setItem('cakecrm_token', t);
    try { localStorage.setItem('cakecrm_theme', dark ? 'dark' : 'light'); } catch { /* storage blocked */ }
  }, [TOKEN, auth, dark]);
  return ctx.newPage();
}
// Hide the example-data banner and the floating assistant launcher, which would sit on every shot.
async function clean(page, { banner = false } = {}) {
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(700);
  await page.evaluate(banner => {
    if (!banner) for (const el of document.querySelectorAll('div')) {
      if (el.children.length < 8 && (el.textContent || '').startsWith("You're viewing example data")) el.style.display = 'none';
    }
    for (const el of document.querySelectorAll('button,a')) {
      const s = getComputedStyle(el);
      if (s.position === 'fixed' && parseInt(s.bottom) < 80 && parseInt(s.right) < 80) el.style.display = 'none';
    }
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n;
    while ((n = w.nextNode())) n.nodeValue = n.nodeValue.replace(/http:\/\/localhost:\d+/g, 'https://crm.example.com');
    for (const i of document.querySelectorAll('input')) if (/localhost:\d+/.test(i.value)) i.value = i.value.replace(/http:\/\/localhost:\d+/g, 'https://crm.example.com');
  }, banner);
}
const failures = [];
async function shot(name, fn, opts = {}) {
  if (only && !name.includes(only)) return;
  const page = await newPage(opts);
  try {
    await fn(page);
    await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: !!opts.full });
    console.log('ok', name);
  } catch (e) { failures.push(name); console.log('FAIL', name, e.message.split('\n')[0]); }
  await page.context().close();
}
const go = async (page, url, o) => { await page.goto(BASE + url); await clean(page, o); };
const click = async (page, role, name) => { await page.getByRole(role, { name, exact: true }).first().click(); await page.waitForTimeout(700); };

// ── Getting started
await shot('login', async p => { await p.goto(BASE + '/login'); await p.waitForTimeout(800); }, { auth: false });
await shot('sample-data-banner', p => go(p, '/crm', { banner: true }));
await shot('dark-mode', p => go(p, '/crm'), { dark: true });
await shot('mobile-dashboard', p => go(p, '/crm'), { w: 390, h: 844 });
await shot('mobile-menu', async p => { await go(p, '/crm'); await p.getByText('☰').first().click(); await p.waitForTimeout(600); }, { w: 390, h: 844 });

// ── Dashboard + pipeline
await shot('dashboard', p => go(p, '/crm'));
await shot('pipeline-board', p => go(p, '/crm/pipeline'));
await shot('pipeline-list', async p => { await go(p, '/crm/pipeline'); await click(p, 'button', 'List'); });
await shot('deal-detail', async p => { await go(p, '/crm/pipeline?deal=3'); await p.waitForTimeout(800); });
await shot('add-deal', async p => { await go(p, '/crm/pipeline'); await click(p, 'button', 'Add Deal'); });

// ── Contacts & companies
await shot('contacts-list', p => go(p, '/crm/contacts'));
await shot('contact-detail', p => go(p, '/crm/contacts/1'));
await shot('add-contact', async p => { await go(p, '/crm/contacts'); await click(p, 'button', 'Add Contact'); });
await shot('import-modal', async p => { await go(p, '/crm/contacts'); await click(p, 'button', 'Import Contacts'); });
await shot('companies-list', p => go(p, '/crm/companies'));
await shot('company-detail', p => go(p, '/crm/companies/1'));

// ── Todos
for (const [n, url] of [['todos-today', ''], ['todos-inbox', '/inbox'], ['todos-next', '/next'], ['todos-projects', '/projects'],
  ['todos-waiting', '/waiting'], ['todos-someday', '/someday'], ['todos-done', '/done'], ['todos-review', '/review'], ['todos-contexts', '/search']])
  await shot(n, p => go(p, '/crm/todos' + url));
await shot('todos-quick-add', async p => { await go(p, '/crm/todos/inbox'); await p.getByPlaceholder(/Add to inbox/).first().fill('call Maria tomorrow #Wholesale @calls !'); await p.waitForTimeout(700); });
if (!only || 'todos-simple-list'.includes(only)) {
  await api('POST', '/api/crm/todo-mode', { mode: 'normal' }, TOKEN);
  try { await shot('todos-simple-list', p => go(p, '/crm/todos')); }
  finally { await api('POST', '/api/crm/todo-mode', { mode: 'gtd' }, TOKEN); }
}

// ── Reports, Baker, settings
await shot('reports', async p => { await go(p, '/crm/reports'); const c = p.getByRole('combobox', { name: 'Company' }); await c.click(); await c.fill('Green'); await p.waitForTimeout(500); await p.keyboard.press('ArrowDown'); await p.keyboard.press('Enter'); await p.waitForTimeout(1200); });
await shot('ai-setup', p => go(p, '/setup'));
await shot('memory', async p => { await go(p, '/crm/memory'); await p.getByText('soul', { exact: true }).first().click(); await p.waitForTimeout(800); });
for (const s of ['personal', 'assistant', 'workspace', 'integrations'])
  await shot(`settings-${s}`, p => go(p, `/crm/settings?section=${s}`), { full: true });

console.log('failures:', failures.join(' ') || 'none');
await browser.close();
process.exit(failures.length ? 1 : 0);
