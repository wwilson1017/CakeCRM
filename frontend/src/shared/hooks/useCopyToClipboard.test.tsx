// @vitest-environment jsdom
//
// The shared clipboard helper, tested directly the way this repo's other shared
// hooks are. Three things here are invisible to tsc and to the consuming
// components' own tests: WHICH of the three write paths runs (async API,
// present-but-rejecting, absent entirely), whether the transient `copied` flag
// resets on its own, and whether a copy still in flight when the owner unmounts
// leaves a timer behind.
//
// The middle path is the one worth the file: `/todo/{token}` is served over
// plain http on a LAN install, where `navigator.clipboard` is undefined, so the
// fallback is not an edge case there — it is the only path.
//
// Harness is `createRoot` + React's `act`, per CLAUDE.md — no testing-library.
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { copyToClipboard, useCopyToClipboard } from './useCopyToClipboard';

/** Text the legacy `execCommand` path staged, in call order. */
let execCopied: string[];
/** Text the async clipboard API received, in call order. */
let apiCopied: string[];

/** Install a `navigator.clipboard` whose writeText behaves as asked. */
function setClipboard(impl: ((t: string) => Promise<void>) | undefined) {
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: impl ? { writeText: impl } : undefined,
  });
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  execCopied = [];
  apiCopied = [];
  setClipboard((t: string) => { apiCopied.push(t); return Promise.resolve(); });
  // jsdom does not implement execCommand at all; read back the staging textarea
  // the fallback mounts, which is what a real 'copy' would take its text from.
  (document as unknown as { execCommand: () => boolean }).execCommand = vi.fn(() => {
    // Read the FOCUSED element, exactly as the real execCommand('copy') does.
    // Querying the staging textarea directly would pass even if the fallback
    // never focused it — and a copy that never takes focus copies whatever the
    // user was last in instead.
    const active = document.activeElement as HTMLTextAreaElement | null;
    execCopied.push(active?.tagName === 'TEXTAREA' ? active.value : '');
    return true;
  });
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('copyToClipboard', () => {
  it('uses the async clipboard API when the context is secure', async () => {
    expect(await copyToClipboard('secure text')).toBe(true);
    expect(apiCopied).toEqual(['secure text']);
    expect(execCopied).toEqual([]);
  });

  // The whole reason this helper exists rather than a bare writeText call.
  it('falls back to the legacy path when there is no clipboard API', async () => {
    setClipboard(undefined);
    expect(await copyToClipboard('lan text')).toBe(true);
    expect(execCopied).toEqual(['lan text']);
  });

  it('falls back to the legacy path when the API is present but refuses', async () => {
    setClipboard(() => Promise.reject(new Error('permission denied')));
    expect(await copyToClipboard('denied text')).toBe(true);
    expect(execCopied).toEqual(['denied text']);
  });

  it('reports failure when neither path can write', async () => {
    setClipboard(undefined);
    (document as unknown as { execCommand: () => boolean }).execCommand = vi.fn(() => false);
    expect(await copyToClipboard('nowhere')).toBe(false);
  });

  it('leaves no staging node behind', async () => {
    setClipboard(undefined);
    await copyToClipboard('tidy');
    expect(document.querySelector('textarea')).toBeNull();
  });
});

/** Minimal hook harness — publishes through an effect, so only COMMITTED renders. */
function renderHook(resetMs?: number) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const result: { current: ReturnType<typeof useCopyToClipboard> } = {
    current: undefined as unknown as ReturnType<typeof useCopyToClipboard>,
  };
  function Probe() {
    const value = useCopyToClipboard(resetMs);
    useEffect(() => { result.current = value; });
    return null;
  }
  let root!: Root;
  act(() => { root = createRoot(container); root.render(<Probe />); });
  return {
    result,
    unmount: () => { act(() => root.unmount()); container.remove(); },
  };
}

describe('useCopyToClipboard', () => {
  it('reports a copy and clears the report on its own', async () => {
    vi.useFakeTimers();
    const { result, unmount } = renderHook(1500);
    expect(result.current.status).toBe('idle');

    await act(async () => { await result.current.copy('hello'); });
    expect(result.current.status).toBe('copied');

    await act(async () => { vi.advanceTimersByTime(1500); });
    expect(result.current.status).toBe('idle');
    unmount();
  });

  it('never claims success when the copy failed', async () => {
    setClipboard(undefined);
    (document as unknown as { execCommand: () => boolean }).execCommand = vi.fn(() => false);
    const { result, unmount } = renderHook();

    let ok = true;
    await act(async () => { ok = await result.current.copy('doomed'); });
    expect(ok).toBe(false);
    expect(result.current.status).toBe('failed');
    unmount();
  });

  // The bug this ordering exists to prevent: copy, edit, retry within resetMs
  // and have the retry FAIL. Reporting the first attempt's success against the
  // second attempt's text is worse than saying nothing.
  it('does not let a previous success vouch for a failed retry', async () => {
    vi.useFakeTimers();
    const { result, unmount } = renderHook(1500);

    await act(async () => { await result.current.copy('first'); });
    expect(result.current.status).toBe('copied');

    setClipboard(undefined);
    (document as unknown as { execCommand: () => boolean }).execCommand = vi.fn(() => false);
    await act(async () => { await result.current.copy('second'); });

    expect(result.current.status).toBe('failed');
    unmount();
  });

  // Two writes in flight at once resolve in whatever order the browser manages,
  // which is not click order — only the newest click may set the status.
  it('lets only the newest attempt report', async () => {
    // The slow attempt is rigged to FAIL and the fast one to succeed, so the two
    // outcomes are distinguishable: if a stale attempt could still report, the
    // button would flip from Copied to Copy failed after the user already saw
    // the confirmation for a different click.
    let failFirst!: (e: Error) => void;
    setClipboard((t: string) => {
      if (t === 'slow') return new Promise<void>((_, reject) => { failFirst = reject; });
      apiCopied.push(t);
      return Promise.resolve();
    });
    (document as unknown as { execCommand: () => boolean }).execCommand = vi.fn(() => false);
    const { result, unmount } = renderHook();

    let slow!: Promise<boolean>;
    act(() => { slow = result.current.copy('slow'); });
    await act(async () => { await result.current.copy('fast'); });
    expect(result.current.status).toBe('copied');

    await act(async () => { failFirst(new Error('denied')); await slow; });
    expect(result.current.status).toBe('copied');
    unmount();
  });

  // Tap Copy, then close the sheet before the write resolves. Clearing the timer
  // on unmount does not cover this by itself: the resolving promise would start a
  // brand-new one, after the cleanup meant to end them.
  it('starts no reset timer for a copy that resolves after unmount', async () => {
    let release!: () => void;
    setClipboard(() => new Promise<void>(resolve => { release = resolve; }));
    const { result, unmount } = renderHook();

    let pending!: Promise<boolean>;
    act(() => { pending = result.current.copy('in flight'); });
    unmount();

    // Assert on the hook's own observable rather than on the global setTimeout:
    // "nothing scheduled any macrotask this tick" is a stronger claim than this
    // hook makes, and a scheduler change elsewhere would fail it here.
    await act(async () => { release(); await pending; });
    expect(result.current.status).toBe('idle');
  });
});
