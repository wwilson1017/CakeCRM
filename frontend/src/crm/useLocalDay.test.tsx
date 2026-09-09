// @vitest-environment jsdom
/**
 * The day-rollover hook (#77).
 *
 * Reading the clock inside a predicate is necessary but not sufficient — predicates only
 * run when React re-renders, and time passing is not a render. This hook is what makes the
 * "Overdue" / "Due today" boundaries actually move on a tab left open overnight, so its
 * timing is worth pinning rather than assuming.
 */
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useLocalDay, type LocalDay } from './useLocalDay';
import { ymd } from './pipelineFilters';

let container: HTMLDivElement;
let root: Root;
const latest: { current: LocalDay | null } = { current: null };

function Probe() {
  const value = useLocalDay();
  useEffect(() => { latest.current = value; });
  return null;
}

const state = (): LocalDay => {
  if (!latest.current) throw new Error('probe did not render');
  return latest.current;
};

beforeEach(() => {
  vi.useFakeTimers();
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

describe('useLocalDay', () => {
  it("starts on the viewer's local day", () => {
    // TZ is pinned to America/Chicago by the vitest config, so a late-evening local time
    // is already the NEXT day in UTC — the drift this whole mechanism exists to avoid.
    vi.setSystemTime(new Date(2026, 4, 1, 23, 30));
    act(() => { root.render(<Probe />); });
    expect(state().today).toBe('2026-05-01');
  });

  it('advances when local midnight passes, without polling', () => {
    vi.setSystemTime(new Date(2026, 4, 1, 23, 59, 30));
    act(() => { root.render(<Probe />); });
    expect(state().today).toBe('2026-05-01');

    // Just short of midnight: nothing has moved yet.
    act(() => { vi.advanceTimersByTime(25_000); });
    expect(state().today).toBe('2026-05-01');

    // Past it: the day rolls over, which is what re-runs the date facets and columns.
    act(() => { vi.advanceTimersByTime(10_000); });
    expect(state().today).toBe('2026-05-02');
  });

  it('re-arms, so it keeps working on a second night', () => {
    vi.setSystemTime(new Date(2026, 4, 1, 23, 59, 30));
    act(() => { root.render(<Probe />); });
    act(() => { vi.advanceTimersByTime(35_000); });
    expect(state().today).toBe('2026-05-02');

    // A single setTimeout that was never re-armed would leave this stuck at the 2nd.
    act(() => { vi.advanceTimersByTime(24 * 60 * 60 * 1000); });
    expect(state().today).toBe('2026-05-03');
  });

  it('re-arms even when the timer fires on the SAME day', () => {
    // setToday(sameValue) is a React bail-out — no re-render, no effect, no new timer — so
    // re-arming cannot ride the day value. Reachable when the clock is stepped backwards
    // (NTP correction, VM restore) after the timeout was armed, which would otherwise
    // freeze the day for the life of the tab.
    vi.setSystemTime(new Date(2026, 4, 1, 23, 59, 30));
    act(() => { root.render(<Probe />); });

    // Fire the pending timeout while it is still the 1st: the value does not change…
    vi.setSystemTime(new Date(2026, 4, 1, 22, 0));
    act(() => { vi.advanceTimersByTime(60_000); });
    expect(state().today).toBe('2026-05-01');

    // …but a fresh timer must still be armed, so the real midnight is not missed.
    vi.setSystemTime(new Date(2026, 4, 2, 0, 0, 5));
    act(() => { vi.advanceTimersByTime(2 * 60 * 60 * 1000); });
    expect(state().today).toBe('2026-05-02');
  });

  it('keeps `now` consistent with `today`, and stable within the day', () => {
    vi.setSystemTime(new Date(2026, 4, 1, 23, 30));
    act(() => { root.render(<Probe />); });
    const first = state().now;
    // Derived from the day string, so the two can never disagree…
    expect(ymd(first)).toBe(state().today);
    // …and its identity is stable, which is what lets it sit in a config's memo deps
    // without rebuilding the search index on every render.
    act(() => { vi.advanceTimersByTime(60_000); });
    expect(state().now).toBe(first);
  });
});
