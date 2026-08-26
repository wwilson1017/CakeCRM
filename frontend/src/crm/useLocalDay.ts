/**
 * The viewer's local calendar day, which re-renders its consumers when the day rolls over
 * (issue #77).
 *
 * Reading `new Date()` inside a facet predicate or a cell renderer is necessary but NOT
 * sufficient: those only run again when React re-renders, and nothing about the passage of
 * time causes a render. A CRM tab left open overnight — an ordinary thing to do — would
 * therefore keep yesterday's "today": tasks that became overdue at midnight would still
 * render as due today, and the "Due today" / "Overdue" / "Next 7 days" / "Last contact"
 * facets would keep selecting against a boundary that has moved.
 *
 * So the day is state, and it advances on a timer aimed at the next local midnight. Feeding
 * `now` into the collection configs makes the dependency real rather than incidental: the
 * config identity changes once a day, which is what makes the layer recompute its filtered
 * set. (Once a day is far below the identity-churn the layer warns about.)
 */
import { useEffect, useMemo, useState } from 'react';

import { ymd } from './pipelineFilters';

/** Milliseconds from `now` until the next local midnight, plus a second of slack. */
function untilNextLocalDay(now: Date): number {
  // Built from calendar fields, not `+ 86_400_000`, so a DST transition still lands on the
  // start of the next calendar day rather than an hour early or late.
  const next = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 0, 0, 1);
  return Math.max(1000, next.getTime() - now.getTime());
}

export interface LocalDay {
  /** Local `YYYY-MM-DD`. Stable for the whole day, so it is safe in a dependency array. */
  today: string;
  /** A Date inside `today` — recreated only when the day changes, so its identity is stable too. */
  now: Date;
}

export function useLocalDay(): LocalDay {
  const [today, setToday] = useState(() => ymd(new Date()));

  useEffect(() => {
    // Re-armed whenever the day changes, so this schedules one hop per day rather than a
    // polling interval. A machine waking from sleep past midnight fires it late, and the
    // recomputed value is still correct because it is read from the clock, not counted.
    //
    // Delay 0 when the day has ALREADY moved: `today` is captured during render but this
    // runs later, so midnight can fall in between (React may defer passive effects). Left
    // alone, that would arm the next hop ~24h out while rendering a stale day for the
    // whole of it. Correcting on the next tick — rather than setting state in the effect
    // body — also keeps this clear of react-hooks/set-state-in-effect.
    const now = new Date();
    const delay = ymd(now) === today ? untilNextLocalDay(now) : 0;
    const timer = setTimeout(() => setToday(ymd(new Date())), delay);
    return () => clearTimeout(timer);
  }, [today]);

  // DERIVED from `today` rather than read from the clock again, so the two can never
  // disagree — and at local NOON, which keeps the calendar-field arithmetic the recency
  // helpers do (`ymd(now, -7)`) clear of both DST boundaries.
  const now = useMemo(() => {
    const [y, m, d] = today.split('-').map(Number);
    return new Date(y, m - 1, d, 12);
  }, [today]);
  return { today, now };
}
