// Issue #149, the second half of the guard — and the half `bootSplit.test.ts` structurally
// cannot be.
//
// That file reads SOURCE: it proves the import statements say the right thing. This one builds
// the app and reads the emitted chunk graph, because two real regressions leave every source
// assertion green:
//
//   1. **An eager leaf grows a heavy import.** `App.tsx`'s allowlist is a list of DIRECT
//      specifiers. Nothing in it stops `LoginPage` — an allowlisted eager leaf — from importing
//      the assistant tomorrow, which would put react-markdown + highlight.js back in the shell
//      chunk every CRM visitor downloads. A per-file allowlist can only ever see one hop.
//   2. **The bundler changes its mind.** The whole chunk topology here is Rolldown's automatic
//      shared-chunk behaviour over the import graph — `vite.config.ts` declares no
//      `manualChunks` and no `codeSplitting` options at all, deliberately. A Vite/Rolldown
//      upgrade that merged both dynamic branches into one chunk would undo this issue entirely
//      while every `lazy()` in the source stayed exactly as written.
//
// So this asserts the OUTPUT, and it asserts it in terms of SOURCE MODULES rather than chunk
// filenames: for each surface, the set of modules the browser actually downloads (a chunk plus
// the transitive closure of its STATIC imports, which is precisely what Vite's `__vitePreload`
// fetches) must not contain modules that surface has no business paying for. Naming chunks
// instead would be brittle in a way that fails open — an earlier revision of this file matched
// chunks by filename and a content hash happened to contain the letters `dnd`.
//
// Dynamic imports are deliberately NOT followed: the dynamic edge IS the boundary under test.
//
// It builds in memory (`write: false`) in well under a second, so it earns its place in the
// normal `npm test` sweep rather than needing a `dist/` only `npm run build` produces.
import { build } from 'vite';
import { describe, expect, it } from 'vitest';

type Chunk = {
  fileName: string;
  isEntry: boolean;
  facadeModuleId: string | null;
  modules: Record<string, unknown>;
  imports: string[];
  code: string;
};

const ROOT = new URL('..', import.meta.url).pathname;

/**
 * Build the real app with the real `vite.config.ts` and return its JS chunks.
 *
 * `mode: 'production'` is explicit: under vitest `NODE_ENV` is `test`, which would hand the
 * React plugin its dev JSX runtime. That does not change the chunk TOPOLOGY asserted below, but
 * building in the mode we ship is the point of building at all.
 */
async function buildChunks(): Promise<Chunk[]> {
  // `mode: 'production'` alone is NOT enough, and the difference is measurable rather than
  // theoretical: vitest sets `NODE_ENV=test`, and @vitejs/plugin-react reads THAT to choose its
  // JSX runtime, so the build came out with `jsx-dev-runtime` and far less minification — a
  // 369 kB entry chunk against the 187 kB we actually ship. Every byte budget below would then
  // have been measuring a build no user receives. Set it for the duration and put it back.
  const previousNodeEnv = process.env.NODE_ENV;
  process.env.NODE_ENV = 'production';
  let result;
  try {
    result = await build({
      root: ROOT,
      mode: 'production',
      logLevel: 'silent',
      build: { write: false },
    });
  } finally {
    process.env.NODE_ENV = previousNodeEnv;
  }
  // `build()` is typed as output-or-watcher because the same call can watch; we never pass
  // `watch`, so narrow on the shape rather than asserting across the union.
  const bundle = Array.isArray(result) ? result[0] : result;
  if (!bundle || !('output' in bundle)) throw new Error('vite build returned a watcher, not a bundle');
  const output = bundle.output as unknown as Array<Record<string, unknown>>;
  return output.filter((asset) => asset.type === 'chunk') as unknown as Chunk[];
}

const CHUNKS = await buildChunks();
const BY_FILE = new Map(CHUNKS.map((c) => [c.fileName, c]));

/** A module id as a repo-relative path: '/abs/.../frontend/src/App.tsx' -> 'src/App.tsx'. */
const rel = (id: string) => (id.startsWith(ROOT) ? id.slice(ROOT.length) : id).replace(/^\/+/, '');

/** The chunk whose facade is this source module — i.e. the chunk a `lazy(() => import(x))` fetches. */
function chunkFor(source: string): Chunk {
  const hit = CHUNKS.find((c) => c.facadeModuleId && rel(c.facadeModuleId) === source);
  if (!hit) {
    throw new Error(
      `no chunk has '${source}' as its facade — it is no longer a code-split boundary, ` +
        `or it moved. Facades: ${CHUNKS.filter((c) => c.facadeModuleId).map((c) => rel(c.facadeModuleId!)).join(', ')}`,
    );
  }
  return hit;
}

/** The single entry chunk — what `index.html` loads, downloaded by every visitor. */
const ENTRY = (() => {
  const entries = CHUNKS.filter((c) => c.isEntry);
  if (entries.length !== 1) throw new Error(`expected exactly one entry chunk, got ${entries.length}`);
  return entries[0];
})();

/**
 * Every SOURCE MODULE a surface downloads: the named chunks plus the transitive closure of their
 * static imports. Throws on an unknown chunk rather than returning a smaller set — a vanished
 * chunk must fail loudly, not quietly empty every "must not contain" below into a tautology.
 */
function modulesDownloadedBy(chunks: Chunk[]): Set<string> {
  const seen = new Set<string>();
  const modules = new Set<string>();
  const stack = chunks.map((c) => c.fileName);
  while (stack.length) {
    const fileName = stack.pop()!;
    if (seen.has(fileName)) continue;
    seen.add(fileName);
    const chunk = BY_FILE.get(fileName);
    if (!chunk) throw new Error(`chunk '${fileName}' is imported but not emitted`);
    for (const id of Object.keys(chunk.modules)) modules.add(rel(id));
    for (const dep of chunk.imports) stack.push(dep);
  }
  return modules;
}

/** Assert no downloaded module matches any forbidden pattern, naming the offender if one does. */
function forbid(what: string, modules: Set<string>, patterns: Array<[string, RegExp]>) {
  expect(patterns.length).toBeGreaterThan(0);
  for (const [label, pattern] of patterns) {
    const offenders = [...modules].filter((m) => pattern.test(m));
    expect(offenders, `${what} must not download ${label}`).toEqual([]);
  }
}

/** The heavy things, described by what they ARE rather than by a hashed filename. */
const ASSISTANT: [string, RegExp] = ['the assistant', /^src\/assistant\//];
const MARKDOWN: [string, RegExp] = ['react-markdown / highlight.js', /node_modules\/(react-markdown|remark-|rehype-|highlight\.js)/];
const DND: [string, RegExp] = ['@dnd-kit', /node_modules\/@dnd-kit\//];
const COLLECTION: [string, RegExp] = ['the shared collection layer', /^src\/shared\/(collection|dnd|listview|overlay)\//];
const CRM_PAGES: [string, RegExp] = ['a CRM page', /^src\/crm\/(?!gtd\/)[A-Z][^/]*\.tsx$/];

describe('boot split (#149) — the built chunk graph', () => {
  it('emits a distinct chunk for every code-split boundary', () => {
    // If the bundler ever merged these, every assertion below would still pass while the split
    // itself was gone — so pin the topology first, and pin that the chunks are DISTINCT files.
    const boundaries = [
      'src/App.tsx',
      'src/crm/gtd/PublicTodoApp.tsx',
      'src/crm/gtd/pages.ts',
      'src/assistant/AssistantPanelBody.tsx',
      'src/crm/CrmLayout.tsx',
      'src/crm/CrmDashboardPage.tsx',
      'src/crm/ContactsPage.tsx',
      'src/crm/CompaniesPage.tsx',
      'src/crm/PipelinePage.tsx',
        'src/crm/SettingsPage.tsx',
      'src/crm/MemoryPage.tsx',
      'src/crm/TasksPage.tsx',
      'src/setup/SetupPage.tsx',
    ];
    expect(boundaries.length).toBe(13);
    const files = boundaries.map((b) => chunkFor(b).fileName);
    expect(new Set(files).size).toBe(boundaries.length);
    expect(files).not.toContain(ENTRY.fileName);
  });

  it('the /todo PWA downloads no CRM page, no assistant and no collection layer', () => {
    // The issue itself, expressed as an assertion about bytes rather than import statements.
    const modules = modulesDownloadedBy([ENTRY, chunkFor('src/crm/gtd/PublicTodoApp.tsx')]);

    // It must reach the GTD pages, or the assertions below are about an empty download.
    expect(modules).toContain('src/crm/gtd/pages.ts');
    expect(modules).toContain('src/crm/gtd/TodayPage.tsx');
    expect(modules).toContain('src/crm/gtd/TodoShell.tsx');

    forbid('/todo', modules, [
      ASSISTANT, MARKDOWN, DND, COLLECTION, CRM_PAGES,
      ['the CRM shell', /^src\/App\.tsx$/],
      ['the CRM layout', /^src\/crm\/CrmLayout\.tsx$/],
      ['the login page', /^src\/login\//],
      ['the setup wizard', /^src\/setup\//],
      ['the auth context', /^src\/core\/auth\/AuthContext\.tsx$/],
    ]);
  });

  it('the CRM shell downloads no page chunk, no assistant and no collection layer', () => {
    // The assertion `bootSplit.test.ts` cannot make: its App allowlist is one hop deep, so an
    // eager leaf (LoginPage, TasksModeRouter, ToastViewport) growing a heavy import is invisible
    // there and caught here.
    const modules = modulesDownloadedBy([ENTRY, chunkFor('src/App.tsx')]);

    expect(modules).toContain('src/App.tsx');
    expect(modules).toContain('src/login/LoginPage.tsx');

    forbid('the CRM shell', modules, [
      ASSISTANT, MARKDOWN, DND, COLLECTION, CRM_PAGES,
      ['the GTD pages', /^src\/crm\/gtd\/(pages\.ts|TodayPage\.tsx)$/],
      ['the public todo app', /^src\/crm\/gtd\/PublicTodoApp\.tsx$/],
      ['the CRM layout', /^src\/crm\/CrmLayout\.tsx$/],
    ]);
  });

  it('the entry chunk is React, the boot components and nothing else', () => {
    // Whatever `main.tsx` reaches eagerly is paid for by every visitor on every surface, /todo
    // included — the original 1 MB failure in miniature if it grows.
    const modules = modulesDownloadedBy([ENTRY]);
    const own = [...modules].filter((m) => m.startsWith('src/')).sort();
    // An exact list, not a ceiling: the entry is the one chunk where "what is allowed here"
    // has a short, complete answer, and anything added to it is paid for by every visitor.
    // `index.css` is the app's whole stylesheet — CSS carries no JS graph, which is why a
    // stylesheet import is the one thing that cannot re-merge a bundle.
    expect(own).toEqual([
      'src/Root.tsx',
      'src/core/components/BootFallback.tsx',
      'src/core/components/ChunkErrorBoundary.tsx',
      'src/crm/gtd/publicMode.ts',
      'src/index.css',
      'src/main.tsx',
    ]);
    forbid('the entry chunk', modules, [
      ASSISTANT, MARKDOWN, DND, COLLECTION, CRM_PAGES,
      ['the CRM shell', /^src\/App\.tsx$/],
      ['the public todo app', /^src\/crm\/gtd\/PublicTodoApp\.tsx$/],
    ]);
  });

  it('the assistant is the heavy chunk, and nothing reaches it statically', () => {
    // Pins the premise behind lazy-loading it: if react-markdown/highlight.js ever stopped
    // landing here, "the drawer is the heaviest graph in the app" would be stale reasoning that
    // still reads as true.
    const assistant = chunkFor('src/assistant/AssistantPanelBody.tsx');
    const own = Object.keys(assistant.modules).map(rel);
    expect(own.filter((m) => /node_modules\/(react-markdown|highlight\.js)/.test(m)).length).toBeGreaterThan(0);

    // A static edge from any chunk would pull the whole graph into that chunk's download, which
    // is exactly what the `lazy()` in AssistantLauncher exists to prevent.
    for (const chunk of CHUNKS) {
      if (chunk.fileName === assistant.fileName) continue;
      expect(chunk.imports, `${chunk.fileName} must not statically import the assistant`)
        .not.toContain(assistant.fileName);
    }
  });

  it('the entry and /todo downloads stay inside a size budget', () => {
    // The deny-lists above name TODAY's heavy graphs, and that is their limit: `LoginPage` could
    // statically import some large new dependency and every module-pattern assertion would pass
    // while the shell doubled. A budget is the only assertion that catches a heavy import nobody
    // thought to name — it measures the thing the issue is actually about.
    //
    // Numbers are JS bytes only — the emitted chunk code, no CSS and no fonts — so they are
    // smaller than the per-surface figures in the PR description, which include the stylesheet.
    // Each budget carries ~38% headroom over the measured figure so ordinary feature work never
    // trips it. They are a REGRESSION alarm, not a target: if a change legitimately needs more,
    // raise the number in the same commit and say why — that edit is the point, because it is
    // what makes the cost visible to a reviewer instead of silent.
    const jsBytes = (chunks: Chunk[]): number => {
      const seen = new Set<string>();
      const stack = chunks.map((c) => c.fileName);
      let total = 0;
      while (stack.length) {
        const fileName = stack.pop()!;
        if (seen.has(fileName)) continue;
        seen.add(fileName);
        const chunk = BY_FILE.get(fileName);
        if (!chunk) throw new Error(`chunk '${fileName}' is imported but not emitted`);
        total += Buffer.byteLength(chunk.code, 'utf8');
        for (const dep of chunk.imports) stack.push(dep);
      }
      return total;
    };

    const budgets: Array<[string, number, Chunk[]]> = [
      // measured 196.6 kB (entry chunk + the JSX runtime)
      ['the entry chunk every visitor downloads', 272_000, [ENTRY]],
      // measured 312.3 kB — the number this issue exists to hold down
      ['the /todo PWA cold load', 431_000, [ENTRY, chunkFor('src/crm/gtd/PublicTodoApp.tsx')]],
      // measured 259.6 kB
      ['the CRM shell', 358_000, [ENTRY, chunkFor('src/App.tsx')]],
    ];
    expect(budgets.length).toBe(3);
    for (const [label, budget, roots] of budgets) {
      const bytes = jsBytes(roots);
      // A floor as well as a ceiling: a budget that passes because the traversal returned
      // nothing is not a budget. Anything under 50 kB means the graph, not the size, is wrong.
      expect(bytes, `${label} measured a real graph`).toBeGreaterThan(50_000);
      expect(bytes, `${label} is ${(bytes / 1024).toFixed(1)} kB, budget ${(budget / 1024).toFixed(0)} kB`)
        .toBeLessThan(budget);
    }
  });

  it('the traversal itself reaches past the roots', () => {
    // The self-pin. Every test above is a "must not contain" over `modulesDownloadedBy`, so if
    // that function stopped traversing static imports it would return the roots' own modules and
    // every assertion would pass while proving nothing.
    const shellOnly = new Set(Object.keys(chunkFor('src/App.tsx').modules).map(rel));
    const shellClosure = modulesDownloadedBy([ENTRY, chunkFor('src/App.tsx')]);
    expect(shellClosure.size).toBeGreaterThan(shellOnly.size);
    // React itself lives in the entry chunk, so the closure must have crossed a chunk boundary.
    expect([...shellClosure].some((m) => m.includes('node_modules/react-dom'))).toBe(true);

    // And an absent chunk is fatal rather than empty.
    const ghost = { fileName: 'assets/not-emitted.js' } as Chunk;
    expect(() => modulesDownloadedBy([ghost])).toThrow(/imported but not emitted/);
  });
});
