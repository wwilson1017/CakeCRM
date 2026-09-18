// @vitest-environment jsdom
//
// The Cards-tab search box debounces through this hook. The properties that
// matter — the initial value is returned synchronously, the update lands only after
// the delay, and a second keystroke RESTARTS the countdown rather than letting the
// first one land — are all invisible to `tsc` and to eslint, and a regression would
// show up only as a search box that feels laggy or fires on every keystroke.
//
// jsdom is opted into per-file via the docblock above, per AGENTS.md; the harness is
// `createRoot` + React 19's `act`, following `the blueprint's polling-hook test`.
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useDebounce } from './useDebounce';

/**
 * Minimal hook harness. The probe publishes through an EFFECT rather than assigning
 * during render: writing to an outer binding mid-render is what the React Compiler
 * lint rule forbids, and a render may legitimately be discarded by a concurrent
 * renderer. An effect runs only for a COMMITTED render, and `act` flushes effects
 * before returning, so `result.current` is always the latest committed value.
 */
function renderHook<P, R>(useHook: (props: P) => R, initialProps: P) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const result: { current: R } = { current: undefined as unknown as R };
  function Probe({ value }: { value: P }) {
    const hookValue = useHook(value);
    useEffect(() => {
      result.current = hookValue;
    });
    return null;
  }
  let root!: Root;
  act(() => {
    root = createRoot(container);
    root.render(<Probe value={initialProps} />);
  });
  return {
    result,
    rerender: (props: P) => act(() => root.render(<Probe value={props} />)),
    unmount: () => {
      act(() => root.unmount());
      container.remove();
    },
  };
}

afterEach(() => {
  vi.useRealTimers();
});

describe('useDebounce', () => {
  it('returns the initial value straight away', () => {
    vi.useFakeTimers();
    const hook = renderHook<string, string>((v: string) => useDebounce(v, 250), 'first');
    expect(hook.result.current).toBe('first');
    hook.unmount();
  });

  it('updates only once the full delay has elapsed', () => {
    vi.useFakeTimers();
    const hook = renderHook<string, string>((v: string) => useDebounce(v, 250), 'a');

    hook.rerender('ab');
    expect(hook.result.current).toBe('a'); // still inside the window

    act(() => {
      vi.advanceTimersByTime(249);
    });
    expect(hook.result.current).toBe('a');

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(hook.result.current).toBe('ab');
    hook.unmount();
  });

  it('restarts the countdown when the value changes again mid-window', () => {
    vi.useFakeTimers();
    const hook = renderHook<string, string>((v: string) => useDebounce(v, 250), 'a');

    hook.rerender('ab');
    act(() => {
      vi.advanceTimersByTime(200);
    });

    // A second keystroke 200ms in must reset the timer — otherwise the first
    // value would land 50ms later and the search would fire twice.
    hook.rerender('abc');
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(hook.result.current).toBe('a');

    act(() => {
      vi.advanceTimersByTime(50);
    });
    expect(hook.result.current).toBe('abc');
    hook.unmount();
  });

  it('clears its timer on unmount', () => {
    vi.useFakeTimers();
    const hook = renderHook<string, string>((v: string) => useDebounce(v, 250), 'a');
    hook.rerender('ab');
    hook.unmount();
    expect(() =>
      act(() => {
        vi.advanceTimersByTime(500);
      }),
    ).not.toThrow();
  });
});
