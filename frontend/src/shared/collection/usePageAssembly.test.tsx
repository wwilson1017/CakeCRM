// @vitest-environment jsdom
import { StrictMode, act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import usePageAssembly, { MAX_PAGES, PAGE_TIMEOUT_MS } from './usePageAssembly';
import type { AssemblyPage, PageAssembly } from './usePageAssembly';

type Fetcher = (page: number, signal: AbortSignal) => Promise<AssemblyPage<number>>;

let container: HTMLDivElement;
let root: Root;
// House hook-capture idiom (the blueprint's polling-hook test): mutate a property inside an
// effect — reassigning a module variable during render trips react-hooks/globals.
const latest: { current: PageAssembly<number> | null } = { current: null };

function Probe({ fetchPage, enabled }: { fetchPage: Fetcher; enabled?: boolean }) {
  const value = usePageAssembly(fetchPage, enabled);
  useEffect(() => {
    latest.current = value;
  });
  return null;
}

function renderAssembly(fetchPage: Fetcher, enabled?: boolean): void {
  act(() => {
    root.render(
      <StrictMode>
        <Probe fetchPage={fetchPage} enabled={enabled} />
      </StrictMode>,
    );
  });
}

const state = (): PageAssembly<number> => {
  if (!latest.current) throw new Error('probe did not render');
  return latest.current;
};

/** Flush pending microtasks through act so effect-driven state lands. */
async function settle(rounds = 6): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    await act(async () => {
      await Promise.resolve();
    });
  }
}

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  latest.current = null;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe('usePageAssembly', () => {
  it('assembles every page and surfaces the set only once COMPLETE', async () => {
    const pages: AssemblyPage<number>[] = [
      { items: [1, 2], hasMore: true },
      { items: [3], hasMore: true },
      { items: [4, 5], hasMore: false },
    ];
    let resolveGate: (() => void) | null = null;
    const fetchPage: Fetcher = async page => {
      if (page === 1 && resolveGate === null) {
        // Hold page 1 so the mid-assembly state is observable.
        await new Promise<void>(resolve => {
          resolveGate = resolve;
        });
      }
      return pages[page];
    };
    renderAssembly(fetchPage);
    await settle();
    // Mid-assembly: page 0 landed, the set is NOT surfaced (never a partial set).
    expect(state().items).toBeNull();
    expect(state().loading).toBe(true);
    expect(state().pagesLoaded).toBe(1);
    expect(state().itemsLoaded).toBe(2);

    act(() => resolveGate?.());
    await settle();
    expect(state().items).toEqual([1, 2, 3, 4, 5]);
    expect(state().loading).toBe(false);
    expect(state().pagesLoaded).toBe(3);
  });

  it('a fetch failure surfaces a retryable error and retry() restarts from page 0', async () => {
    // Failure is MODE-based, not once-only: StrictMode double-runs the effect, and a
    // consumed-once failure would land on the stale first run and vanish silently.
    let mode: 'fail' | 'ok' = 'fail';
    const seen: number[] = [];
    const fetchPage: Fetcher = async page => {
      seen.push(page);
      if (mode === 'fail') throw new Error('boom');
      return { items: [page], hasMore: page < 1 };
    };
    renderAssembly(fetchPage);
    await settle();
    expect(state().error).toBe('boom');
    expect(state().items).toBeNull();

    mode = 'ok';
    seen.length = 0;
    act(() => state().retry());
    await settle();
    expect(state().error).toBeNull();
    expect(state().items).toEqual([0, 1]);
    // The retry restarted from page 0 — partial progress is never resumed.
    expect(seen[0]).toBe(0);
  });

  it('a fresh fetchPage identity per render does NOT restart the assembly', async () => {
    // The natural consumer shape is an inline arrow — a new identity every render. The
    // assembly must keep running against the latest fetcher, not abort and restart from
    // page 0 (which would self-sustain via this hook's own progress writes).
    let pageZeroCalls = 0;
    const gates: Array<() => void> = [];
    const impl = async (page: number): Promise<AssemblyPage<number>> => {
      if (page === 0) {
        pageZeroCalls++;
        await new Promise<void>(resolve => {
          gates.push(resolve);
        });
      }
      return { items: [page], hasMore: page < 1 };
    };
    renderAssembly(page => impl(page));
    await settle();
    const callsAfterMount = pageZeroCalls; // StrictMode starts (and supersedes) an extra run
    renderAssembly(page => impl(page));
    renderAssembly(page => impl(page));
    await settle();
    expect(pageZeroCalls).toBe(callsAfterMount);
    act(() => gates.forEach(release => release()));
    await settle();
    expect(state().items).toEqual([0, 1]);
  });

  it('retry() mid-flight supersedes the run: the stale rejection writes nothing', async () => {
    // Hang page 1 of the first LIVE run (StrictMode's superseded mount run never reaches
    // page 1 — its token is stale after page 0), retry mid-flight, then reject the hung
    // fetch: the superseded run must say nothing while the fresh run assembles from page 0.
    let hungReject: ((err: Error) => void) | null = null;
    const fetchPage: Fetcher = async (page, signal) => {
      if (page === 1 && !signal.aborted && hungReject === null) {
        await new Promise<void>((_resolve, reject) => {
          hungReject = reject;
        });
      }
      return { items: [page], hasMore: page < 1 };
    };
    renderAssembly(fetchPage);
    await settle();
    expect(state().items).toBeNull();
    expect(state().pagesLoaded).toBe(1); // page 0 landed, page 1 is hung

    act(() => state().retry());
    await settle();
    expect(state().items).toEqual([0, 1]); // fresh run restarted from page 0 and completed

    act(() => hungReject?.(new Error('stale-boom')));
    await settle(2);
    expect(state().error).toBeNull(); // the superseded run's rejection wrote nothing
    expect(state().items).toEqual([0, 1]);
  });

  it('aborts the in-flight fetch on unmount, silently', async () => {
    let aborted = false;
    const fetchPage: Fetcher = (_page, signal) =>
      new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => {
          aborted = true;
          reject(new DOMException('aborted', 'AbortError'));
        });
      });
    renderAssembly(fetchPage);
    await settle(2);
    act(() => root.unmount());
    await Promise.resolve();
    expect(aborted).toBe(true);
    // Remount a fresh root so afterEach's unmount stays valid.
    root = createRoot(container);
  });

  it('a hung page times out into a retryable error', async () => {
    vi.useFakeTimers();
    const fetchPage: Fetcher = (_page, signal) =>
      new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      });
    renderAssembly(fetchPage);
    await act(async () => {
      vi.advanceTimersByTime(PAGE_TIMEOUT_MS + 1);
      await Promise.resolve();
    });
    vi.useRealTimers();
    await settle(2);
    expect(state().error).toMatch(/timed out/);
  });

  it('a server that never stops reporting more pages terminates loudly', async () => {
    const fetchPage: Fetcher = async () => ({ items: [1], hasMore: true });
    renderAssembly(fetchPage);
    // MAX_PAGES sequential awaits need generous flushing.
    await settle(MAX_PAGES + 10);
    expect(state().error).toMatch(/did not terminate/);
    expect(state().items).toBeNull();
  });
});

// the blueprint: "mounted" is not "visible". `apps/crm/CrmPage` renders all four tabs and hides the
// inactive ones with a `hidden` class, so without this gate opening CRM on the Dashboard swept
// the whole Companies AND Contacts corpora immediately.
describe('the enabled gate', () => {
  it('fetches nothing while disabled, and reports loading rather than an empty set', async () => {
    const fetchPage = vi.fn<Fetcher>(async () => ({ items: [1], hasMore: false }));
    renderAssembly(fetchPage, false);
    await settle();
    expect(fetchPage).not.toHaveBeenCalled();
    // `items: []` would let a facet bar and its counts render over nothing — the failure this
    // module exists to prevent. "Not here yet" is the honest report.
    expect(state().items).toBeNull();
    expect(state().loading).toBe(true);
    expect(state().error).toBeNull();
  });

  it('assembles once it is enabled', async () => {
    const fetchPage = vi.fn<Fetcher>(async page => ({ items: [page], hasMore: page < 1 }));
    renderAssembly(fetchPage, false);
    await settle();
    expect(fetchPage).not.toHaveBeenCalled();

    renderAssembly(fetchPage, true);
    await settle();
    expect(state().items).toEqual([0, 1]);
    expect(state().loading).toBe(false);
  });

  it('LATCHES — going back to disabled keeps the set and does not re-sweep', async () => {
    // Switching away from a tab and back must be free, so the gate is one-way once armed.
    const fetchPage = vi.fn<Fetcher>(async () => ({ items: [7], hasMore: false }));
    renderAssembly(fetchPage, true);
    await settle();
    expect(state().items).toEqual([7]);
    const callsAfterAssembly = fetchPage.mock.calls.length;

    renderAssembly(fetchPage, false);
    await settle();
    expect(state().items).toEqual([7]);
    expect(fetchPage.mock.calls.length).toBe(callsAfterAssembly);

    renderAssembly(fetchPage, true);
    await settle();
    expect(fetchPage.mock.calls.length).toBe(callsAfterAssembly);
  });
});
