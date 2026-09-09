/**
 * Pure helpers for the dashboard Today panel (issue #130).
 *
 * The RANKING is the server's — `items` arrives ordered by the priority ladder and the
 * panel renders it as given. What lives here is only what the client decides: how many
 * rows the collapsed card shows, and which owner scope it is asking for.
 */
import type { CrmTodayItem, CrmTodayReminderItem, CrmTodayTaskItem } from '../core/types';
import { CORAL_TEXT, GOLD_TEXT, INK_MUTE, formatNumber } from '../shared/styles';

/** Rows the collapsed panel shows before the "+N more today" expander. */
export const TODAY_COLLAPSED = 5;

export type TodayScope = 'mine' | 'everyone';

export const TODAY_SCOPE_KEY = 'crm_today_scope_v1';

/**
 * Coercer for `loadPersistedState`. Defaults to `'mine'` — the issue's "mine by
 * default" — so junk, absent, or a value written by a future version all land on the
 * scope the panel is designed around rather than silently widening it.
 */
export function coerceTodayScope(raw: unknown): TodayScope {
  return raw === 'everyone' ? 'everyone' : 'mine';
}

export interface TodayCollapse {
  visible: CrmTodayItem[];
  hiddenCount: number;
}

/**
 * Split the list into what the collapsed card shows and what the expander reveals.
 *
 * Both halves come from ONE array, which is why the "+N" can never disagree with the
 * rows — the count is a property of the list, not a separately-queried total.
 *
 * The five visible slots are filled from RANKED rows only. A `rank` of null means the
 * server put the row on no rung of the ladder — today that is a hot deal nobody has
 * neglected yet (#131) — and such a row must appear "only in the +N more today expanded
 * list". Slicing the first five items instead would break that the moment the panel held
 * fewer than five commitments, which is the ordinary case.
 *
 * `hiddenCount` is deliberately computed the same way whether or not the card is
 * expanded: it answers "how many rows does the expander reveal", so it can drive BOTH the
 * "+N more today" control and the "Show less" one. Reporting 0 while expanded would leave
 * the caller with nothing to test but `items.length`, which is wrong for a payload whose
 * hidden rows are unranked rather than merely past the fifth slot.
 */
export function collapseToday(items: CrmTodayItem[], expanded: boolean): TodayCollapse {
  const ranked = items.filter(i => i.rank !== null).slice(0, TODAY_COLLAPSED);
  return {
    visible: expanded ? items : ranked,
    hiddenCount: items.length - ranked.length,
  };
}

/**
 * The why-badge a task or reminder row wears: the ladder made legible.
 *
 * Text only, in existing ink/status colours — deliberately NO `tint()` background. A
 * new tinted surface under ink text has to be added to `inkContrast.test.ts`'s surface
 * registry (#68), and a badge is not worth widening that contract.
 *
 * **Deals are excluded from the parameter type on purpose.** Their badge is #125's
 * `DealTemperatureIcon`, which the issue specifies and which carries the temperature as a
 * shape rather than a word. Narrowing here rather than adding a `hot` case is what makes
 * that a compiler-checked fact: with `CrmTodayItem` this function would answer a deal from
 * its `default` branch and quietly label it "DUE TODAY".
 */
export function whyBadge(
  item: CrmTodayTaskItem | CrmTodayReminderItem,
): { label: string; color: string } {
  switch (item.why) {
    case 'starred':
      return { label: 'STARRED', color: GOLD_TEXT };
    case 'overdue':
      return { label: 'OVERDUE', color: CORAL_TEXT };
    case 'reminder':
      return { label: 'REMINDER', color: INK_MUTE };
    default:
      return { label: 'DUE TODAY', color: INK_MUTE };
  }
}

/**
 * A hot deal's evidence line: how long it has sat, and what it is worth.
 *
 * The text half of the issue's "🔥 idle 12d · $30k"; the flame is `DealTemperatureIcon`
 * in the badge slot. Both numbers matter together — idle time alone does not say whether
 * chasing it is worth the afternoon, and value alone does not say it is slipping. It is
 * also what separates a rank-2 row from an unranked one on screen: the two wear the same
 * flame, and the idle count is the difference between them.
 *
 * `formatNumber` is the app's existing compact form (30K, 1.2M), and the plain `$` prefix
 * is the convention every other single-deal surface uses (the board card, the list column,
 * the detail sheet, both rollups). Reports (#144) declines a currency symbol only because
 * it SUMS across deals that may not share one; a single deal's own row has no such problem
 * to solve.
 */
export function dealEvidence(days: number, value: number): string {
  return `idle ${Math.max(0, Math.trunc(days))}d · $${formatNumber(value)}`;
}

/**
 * Milliseconds until the server's next local midnight, from `next_refresh_at`.
 *
 * Clamped into a sane window rather than trusted: a value already in the past (clock
 * skew, a payload that sat in a backgrounded tab) must refetch promptly instead of
 * arming a negative timer that fires immediately and spins, and an absurd one must not
 * overflow `setTimeout`'s 32-bit delay — which silently fires it at once, forever.
 */
/** How many times a failed load is retried before the panel gives up and stays hidden. */
export const TODAY_MAX_RETRIES = 3;

/**
 * Backoff for retry number `n` (1-based): 2s, 8s, 32s.
 *
 * Bounded and short — a dashboard card that failed to load should recover from a blip
 * without the user reloading, but it must not become a poller against a backend that is
 * genuinely down.
 */
export function retryDelayMs(failures: number): number {
  return 2000 * 4 ** (Math.max(1, failures) - 1);
}

export function msUntilRefresh(nextRefreshAt: string, now: number): number {
  const target = Date.parse(nextRefreshAt);
  if (!Number.isFinite(target)) return 60 * 60 * 1000;
  return Math.min(Math.max(target - now, 30 * 1000), 25 * 60 * 60 * 1000);
}
