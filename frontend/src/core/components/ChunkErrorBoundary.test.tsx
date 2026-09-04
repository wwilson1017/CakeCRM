// @vitest-environment jsdom
//
// ChunkErrorBoundary is the safety net the #149 split created a need for: code-splitting
// introduced a failure the single eager bundle did not have (a chunk hash that no longer
// exists after a deploy), and this is the only thing standing between that and a blank page.
//
// Two things here are easy to get wrong and impossible to notice in production, so both are
// pinned: the reload must fire at most ONCE per tab session (an expiring guard becomes a
// permanent reload cycle when a chunk is genuinely gone), and it must fire ONLY for a
// chunk-load failure (reloading on a render bug replays the crash and hides it).
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import ChunkErrorBoundary, { RELOAD_GUARD_KEY } from './ChunkErrorBoundary';

const CHUNK_TEXT = 'Couldn’t load this page.';
const GENERIC_TEXT = 'Something went wrong on this page.';

let host: HTMLDivElement;
let root: Root | null = null;
let reload: ReturnType<typeof vi.fn>;

/** The real Chrome/Edge wording for a chunk that 404s after a deploy. */
function ChunkGone(): never {
  throw new Error('Failed to fetch dynamically imported module: /assets/PipelinePage-abc123.js');
}

/** An ordinary component bug — NOT something a reload can fix. */
function RenderBug(): never {
  throw new Error("Cannot read properties of undefined (reading 'map')");
}

async function mount(child: React.ReactNode) {
  root = createRoot(host);
  await act(async () => {
    root!.render(<ChunkErrorBoundary>{child}</ChunkErrorBoundary>);
  });
}

/** Replace sessionStorage wholesale, so a test can simulate a blocked or lossy one. */
function stubStorage(storage: Partial<Storage>) {
  vi.stubGlobal('sessionStorage', storage as Storage);
}

/** A working sessionStorage backed by a Map. */
function workingStorage(seed: Array<[string, string]> = []) {
  const store = new Map<string, string>(seed);
  stubStorage({
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
  });
  return store;
}

beforeEach(() => {
  host = document.createElement('div');
  document.body.appendChild(host);
  reload = vi.fn();
  vi.stubGlobal('location', { reload });
  // React logs the caught error; the noise is expected, not a signal.
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(async () => {
  if (root) {
    const r = root;
    root = null;
    await act(async () => { r.unmount(); });
  }
  host.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('ChunkErrorBoundary (#149)', () => {
  it('renders its children untouched when nothing throws', async () => {
    workingStorage();
    await mount(<div>PAGE CONTENT</div>);

    expect(host.textContent).toContain('PAGE CONTENT');
    expect(reload).not.toHaveBeenCalled();
  });

  it('auto-reloads on a chunk-load failure (the deploy-skew case)', async () => {
    const store = workingStorage();
    await mount(<ChunkGone />);

    expect(reload).toHaveBeenCalledTimes(1);
    expect(store.get(RELOAD_GUARD_KEY)).toBeTruthy();
  });

  it('recognises the Firefox, Safari and Vite-CSS wordings too', async () => {
    const wordings = [
      'error loading dynamically imported module',
      'Importing a module script failed.',
      'Unable to preload CSS for /assets/AssistantPanelBody-abc.css',
    ];
    expect(wordings.length).toBe(3);
    for (const message of wordings) {
      workingStorage();
      function Variant(): never { throw new Error(message); }
      await mount(<Variant />);
      expect(reload, message).toHaveBeenCalledTimes(1);

      // Reset between wordings — each needs its own fresh session and root.
      const r = root!;
      root = null;
      await act(async () => { r.unmount(); });
      reload = vi.fn();
      vi.stubGlobal('location', { reload });
    }
  });

  it('does NOT reload on an ordinary render bug — a reload would replay it and hide it', async () => {
    workingStorage();
    await mount(<RenderBug />);

    expect(reload).not.toHaveBeenCalled();
    // Still a fallback rather than the blank page this app produced without a boundary — but
    // it must NOT claim a deploy caused it. "The app was updated" on a deterministic render
    // bug sends the user round a reload loop and teaches them to stop reporting it.
    expect(host.textContent).toContain(GENERIC_TEXT);
    expect(host.textContent).not.toContain(CHUNK_TEXT);
  });

  it('names the cause correctly on a chunk failure', async () => {
    // The other half of the copy split: only a chunk failure is explained by a deploy.
    stubStorage({ getItem: () => null, setItem: () => {} });
    await mount(<ChunkGone />);

    expect(host.textContent).toContain(CHUNK_TEXT);
    expect(host.textContent).not.toContain(GENERIC_TEXT);
  });

  it('announces the failure and takes focus', async () => {
    // The subtree holding focus was just destroyed. Without role="alert" and a focus move a
    // screen-reader user gets silence and no route to the only control on the page.
    stubStorage({ getItem: () => null, setItem: () => {} });
    await mount(<ChunkGone />);

    const alert = host.querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert!.querySelector('h1')?.textContent).toBe(CHUNK_TEXT);
    expect(document.activeElement).toBe(alert);
  });

  it('reloads at most ONCE per session — an expiring guard would cycle forever', async () => {
    // A chunk that is genuinely gone (offline, or an asset that never deploys) fails again
    // after every reload. Anything time-windowed turns that into a permanent reload cycle.
    workingStorage([[RELOAD_GUARD_KEY, String(Date.now() - 60 * 60 * 1000)]]);
    await mount(<ChunkGone />);

    expect(reload).not.toHaveBeenCalled();
    expect(host.textContent).toContain(CHUNK_TEXT);
  });

  it('does NOT auto-reload when storage is blocked — an unpersistable guard cannot stop a cycle', async () => {
    stubStorage({
      getItem: () => null,
      setItem: () => { throw new DOMException('QuotaExceededError'); },
    });
    await mount(<ChunkGone />);

    expect(reload).not.toHaveBeenCalled();
    expect(host.textContent).toContain(CHUNK_TEXT);
  });

  it('does NOT auto-reload when a write silently fails to persist', async () => {
    // The nastier storage failure: setItem neither throws nor stores. Reading back is what
    // catches it; a write-and-assume would reload on every single failure.
    stubStorage({ getItem: () => null, setItem: () => {} });
    await mount(<ChunkGone />);

    expect(reload).not.toHaveBeenCalled();
    expect(host.textContent).toContain(CHUNK_TEXT);
  });

  it('offers a manual Reload button whenever it did not reload on its own', async () => {
    stubStorage({ getItem: () => null, setItem: () => {} });
    await mount(<ChunkGone />);

    const button = host.querySelector('button');
    expect(button?.textContent).toBe('Reload');

    await act(async () => { button!.click(); });
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('does NOT auto-reload while the browser reports itself offline', async () => {
    // The browser words an offline fetch failure exactly like a deploy-skew one, and reloading
    // is the wrong move for it: it discards a page the user can still read — and anything they
    // were typing — for the browser's own offline screen, which the app cannot recover from.
    // The card, with its manual button, is strictly better there.
    const store = workingStorage();
    vi.stubGlobal('navigator', { onLine: false });
    await mount(<ChunkGone />);

    expect(reload).not.toHaveBeenCalled();
    // The attempt must not be SPENT either, or coming back online would find the one
    // auto-recovery already used up on a failure it was never going to fix.
    expect(store.get(RELOAD_GUARD_KEY)).toBeUndefined();
    expect(host.textContent).toContain(CHUNK_TEXT);
    expect(host.querySelector('button')?.textContent).toBe('Reload');
  });

  it('catches a lazy() chunk whose import REJECTS, not just a component that throws', async () => {
    // Every case above throws during render. The failure this boundary actually exists for is a
    // different mechanism: `lazy()` fetches a chunk, the fetch 404s after a deploy, and the
    // PROMISE rejects — React then re-throws it through the nearest boundary. This pins the
    // whole composition Root.tsx builds (boundary → Suspense → lazy), which no other test here
    // exercises; `bootSplit` proves the JSX nests, this proves nesting that way works.
    const { lazy, Suspense } = await import('react');
    const Missing = lazy(() =>
      Promise.reject(new Error('Failed to fetch dynamically imported module: /assets/Gone-abc123.js')),
    );
    const store = workingStorage();

    root = createRoot(host);
    await act(async () => {
      root!.render(
        <ChunkErrorBoundary>
          <Suspense fallback={<div>LOADING</div>}>
            <Missing />
          </Suspense>
        </ChunkErrorBoundary>,
      );
    });
    // Let the rejected import settle and React commit the boundary's fallback.
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });

    expect(host.textContent).not.toContain('LOADING');
    expect(host.textContent).toContain(CHUNK_TEXT);
    expect(reload).toHaveBeenCalledTimes(1);
    expect(store.get(RELOAD_GUARD_KEY)).toBeTruthy();
  });
});
