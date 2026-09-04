// @vitest-environment jsdom
//
// The no-login todo surface (#70) is decided at module load from the basename the backend
// injected, and since #149 that decision lives in `Root.tsx` with BOTH branches lazy. This
// renders the REAL boot path — `Root`, the exact component `main.tsx` renders — not
// PublicTodoApp in isolation, so a wrong branch, a lost basename, or a Suspense that never
// resolves fails here rather than as a blank page on someone's phone.
//
// What a render CAN'T see is the payload half of #149 (that Root's branches stay lazy and
// App's pages are not statically imported) — that is `src/bootSplit.test.ts`. These two files
// are jointly the guard: this one proves the surface works, that one proves the split holds.
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The todo pages fetch on mount. Nothing here asserts on their data — only that the shell
// boots — so one empty-ish response for every call keeps the render deterministic. Every
// export of ./api is named so a page reaching for a new one fails loudly, not as undefined.
vi.mock('./api', () => ({
  listTodos: vi.fn().mockResolvedValue([]),
  todayTodos: vi.fn().mockResolvedValue([]),
  createTodo: vi.fn(),
  updateTodo: vi.fn(),
  deleteTodo: vi.fn(),
  bulkUpdate: vi.fn(),
  listProjects: vi.fn().mockResolvedValue([]),
  createProject: vi.fn(),
  updateProject: vi.fn(),
  deleteProject: vi.fn(),
  getFilters: vi.fn().mockResolvedValue({ contexts: [], tags: [], status_counts: {} }),
}));

const TOKEN_BASE = '/todo/SECRETTOKEN';
/** TodoShell's tagline — rendered by every GTD page in both modes, and by nothing in the CRM. */
const TODO_SHELL_MARKER = 'Mind like water.';

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot> | null = null;

/** Put the window on a path, with or without the server's base injection. Both are read at
 *  publicMode's module load, so every test re-imports Root after calling this. */
function visit(pathname: string, injectedBase?: string): void {
  const w = window as unknown as { __CAKECRM_TODO_BASE__?: string };
  if (injectedBase) w.__CAKECRM_TODO_BASE__ = injectedBase;
  else delete w.__CAKECRM_TODO_BASE__;
  window.history.replaceState({}, '', pathname);
}

beforeEach(() => {
  // jsdom ships no matchMedia, and useIsMobile (ToastViewport, CrmLayout) calls it on mount.
  // Stubbed per-file rather than in a shared setup file, per the repo's opt-in rule.
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: false, media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  }));
  visit(TOKEN_BASE, TOKEN_BASE);
  host = document.createElement('div');
  document.body.appendChild(host);
});

afterEach(async () => {
  // Unmount, don't just detach the container: the CRM case boots the REAL App, whose
  // AuthProvider owns a BroadcastChannel. Dropping the host node runs none of the cleanups.
  if (root) {
    const r = root;
    root = null;
    await act(async () => { r.unmount(); });
  }
  host.remove();
  vi.unstubAllGlobals();
  vi.resetModules();
});

/** Boot the real Root against whatever `visit()` last set up. Each call re-imports the whole
 *  graph, because `publicMode` decides at module load. The render alone commits only the
 *  Suspense fallback; call `settle()` after it. */
async function bootApp(): Promise<void> {
  const { default: Root } = await import('../../Root');
  root = createRoot(host);
  await act(async () => {
    root!.render(<Root />);
  });
}

const BOOT_TIMEOUT_MS = 30_000;
/** Strictly under BOOT_TIMEOUT_MS so `settle` throws its own diagnostic — naming the marker it
 *  waited for and what was actually on screen — before vitest's generic "test timed out". */
const SETTLE_TIMEOUT_MS = 20_000;

/**
 * Drive every pending React.lazy boundary on this path to a commit, then return.
 *
 * A boot crosses lazy boundaries — Root → PublicTodoApp for the public cases, Root → App →
 * the route element for the CRM case. Each cycle yields a MACROTASK inside `act`, so an
 * import needing more than a microtask still resolves and React commits the retry before
 * `act` exits; looping until an observable marker appears keeps this independent of how many
 * boundaries a given path happens to cross.
 *
 * Do NOT "simplify" this to `await act(async () => { await vi.waitFor(...) })` — that
 * deadlocks: `act` defers the flush until its scope exits, which is the very commit `waitFor`
 * would be polling for. The loop is bounded by WALL TIME, not a cycle count.
 */
async function settle(marker: string, timeoutMs = SETTLE_TIMEOUT_MS): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (host.textContent?.includes(marker)) return;
    await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
  }
  throw new Error(`lazy boundaries never settled on ${JSON.stringify(marker)}; `
    + `host.textContent was ${JSON.stringify(host.textContent)}`);
}

describe('the /todo surface boots through Root (#149)', () => {
  it('renders the todo app, on its own basename, and none of the CRM', async () => {
    await bootApp();
    await settle(TODO_SHELL_MARKER);

    // Assert on real todo chrome, not just length: the tab strip every page renders via
    // TodoShell. And NOT the login page — a dispatch that fell through to App would render
    // ProtectedRoute's redirect to /login inside the CRM router.
    expect(host.textContent).toContain('Inbox');
    expect(host.textContent).toContain('Today');
    expect(host.textContent).not.toContain('Sign in');

    // Every in-app link goes through todoPath(); the router basename is what turns those into
    // /todo/{token}/... . A bare /inbox here would mean the basename was lost and a tap would
    // leave the no-login surface for the authed app.
    //
    // RESOLVED, not raw. Filtering to hrefs that start with '/' would skip exactly the
    // dangerous class: a relative `href="inbox"` written into a page one day resolves against
    // the CURRENT url, so from /todo/SECRETTOKEN/inbox it lands on /todo/SECRETTOKEN/inbox
    // (fine) but from /todo/SECRETTOKEN it lands on /todo/inbox — the token silently dropped,
    // a 404, and an installed PWA that dead-ends. `a.href` is the resolved absolute URL, so
    // relative and absolute are checked the same way and neither can slip past.
    const anchors = [...host.querySelectorAll('a')];
    const sameOrigin = anchors
      .map((a) => new URL(a.href, window.location.href))
      .filter((u) => u.origin === window.location.origin);
    expect(sameOrigin.length).toBeGreaterThan(0);
    const escaping = sameOrigin
      .map((u) => u.pathname)
      .filter((p) => p !== TOKEN_BASE && !p.startsWith(`${TOKEN_BASE}/`));
    expect(escaping, 'every in-app link stays inside the token base').toEqual([]);
  }, BOOT_TIMEOUT_MS);

  // The basename exists so a sub-path resolves to its own page. Without this case the suite
  // would stay green through a basename regression that sent every deep link and every
  // reload to Today via the `*` → <Navigate to="/"> catch-all — presenting as "the installed
  // app always forgets which tab I was on". Reload-on-a-sub-path is the dominant PWA gesture.
  it('resolves a deep link to its own page, not the catch-all', async () => {
    visit(`${TOKEN_BASE}/inbox`, TOKEN_BASE);
    await bootApp();
    await settle('Inbox zero');

    // Only InboxPage renders this empty state, and the mocked API returns no todos, so it is
    // a page identity that does not depend on styling or fixture data.
    expect(host.textContent).toContain('Inbox zero');
  }, BOOT_TIMEOUT_MS);

  // The other half of the same switch, and the more dangerous half: `isTodoPublicMode` decides
  // whether the ENTIRE CRM mounts. A condition that went true for ordinary traffic would
  // replace the CRM with a todo list for everyone, and the cases above — which always inject
  // a base — would still pass.
  it('leaves the CRM tree alone on an ordinary path', async () => {
    visit('/login');
    await bootApp();
    await settle('Sign in');

    // The login page is the one route a signed-out visitor lands on with no data of its own,
    // so it is the cheapest proof the CRM router mounted — and the todo shell did not.
    expect(host.textContent).toContain('Sign in');
    expect(host.textContent).not.toContain(TODO_SHELL_MARKER);
  }, BOOT_TIMEOUT_MS);
});
