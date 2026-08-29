/**
 * Pure helpers for the dashboard Today panel (issue #130).
 *
 * The RANKING is the server's — `items` arrives ordered by the priority ladder and the
 * panel renders it as given. What lives here is only what the client decides: how many
 * rows the collapsed card shows, and which owner scope it is asking for.
 */
import type { CrmTodayItem } from '../core/types';
import { CORAL, GOLD, INK_MUTE } from '../shared/styles';

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
 * Split the ranked list into what the card shows and what the expander reveals.
 *
 * Both halves come from ONE array, which is why the "+N" can never disagree with the
 * rows — the count is a property of the list, not a separately-queried total.
 */
export function collapseToday(items: CrmTodayItem[], expanded: boolean): TodayCollapse {
  if (expanded) return { visible: items, hiddenCount: 0 };
  return {
    visible: items.slice(0, TODAY_COLLAPSED),
    hiddenCount: Math.max(0, items.length - TODAY_COLLAPSED),
  };
}

/**
 * The why-badge each row wears: the ladder made legible.
 *
 * Text only, in existing ink/status colours — deliberately NO `tint()` background. A
 * new tinted surface under ink text has to be added to `inkContrast.test.ts`'s surface
 * registry (#68), and a badge is not worth widening that contract.
 */
export function whyBadge(item: CrmTodayItem): { label: string; color: string } {
  switch (item.why) {
    case 'starred':
      return { label: 'STARRED', color: GOLD };
    case 'overdue':
      return { label: 'OVERDUE', color: CORAL };
    case 'reminder':
      return { label: 'REMINDER', color: INK_MUTE };
    default:
      return { label: 'DUE TODAY', color: INK_MUTE };
  }
}

/**
 * Milliseconds until the server's next local midnight, from `next_refresh_at`.
 *
 * Clamped into a sane window rather than trusted: a value already in the past (clock
 * skew, a payload that sat in a backgrounded tab) must refetch promptly instead of
 * arming a negative timer that fires immediately and spins, and an absurd one must not
 * overflow `setTimeout`'s 32-bit delay — which silently fires it at once, forever.
 */
export function msUntilRefresh(nextRefreshAt: string, now: number): number {
  const target = Date.parse(nextRefreshAt);
  if (!Number.isFinite(target)) return 60 * 60 * 1000;
  return Math.min(Math.max(target - now, 30 * 1000), 25 * 60 * 60 * 1000);
}
