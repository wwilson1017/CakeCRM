/**
 * pipelineBoard — the pure board arithmetic behind PipelinePage (issue #74).
 *
 * Everything here is a plain function over a deal array so it can be unit-tested without a DOM
 * and reused by both the board and the list view. Three things live here rather than inline in
 * the page: the canonical ORDER the collection layer is handed, WHICH stage columns render, and
 * the header totals — each of which the page previously derived in a `useMemo` that also owned
 * rendering.
 *
 * Stage visibility is client state here, unlike the blueprint. cake_os stores `hidden` on a
 * `crm_pipeline_stages` row; CakeCRM's stages are the `STAGE_ORDER` string constants, so there
 * is no row to persist to and sessionStorage is the honest equivalent. What is preserved
 * exactly is the *arrangement*: a hidden stage's deals are removed BEFORE the collection layer
 * sees them, so the totals, the list view, search, sort and the bulk intersection all exclude
 * them by construction rather than by a second filter each would have to remember to apply.
 */

import type { CrmDeal } from '../core/types';
import { OPEN_STAGES, STAGE_ORDER } from './constants';
import { formatAge } from './gtd/util';

const STAGE_RANK = new Map(STAGE_ORDER.map((s, i) => [s, i]));

const HIDDEN_STAGES_KEY = 'crm_pipeline_hidden_stages';

/** Toggle key for one stage column, as declared in `CollectionConfig.toggles`. */
export function stageToggleKey(stage: string): string {
  return `stage:${stage}`;
}

/** Inverse of `stageToggleKey`; returns '' for a key that isn't one of this board's. */
export function stageFromToggleKey(key: string): string {
  return key.startsWith('stage:') ? key.slice('stage:'.length) : '';
}

/** Title-cased stage name for display. Stages are lowercase constants (`lead`, `qualified`…). */
export function stageLabel(stage: string): string {
  return stage.charAt(0).toUpperCase() + stage.slice(1);
}

/**
 * Board order — the canonical `items` order handed to the collection layer, and what its
 * `boardOrder` (`arrayOrder`) sort field reads back at rest.
 *
 * Stage-major in STAGE_ORDER, then `lead_score` DESC with unscored rows last (#18), then the
 * input order (the server's `updated_at DESC`) as a stable tie-break — which is exactly the
 * per-column order the pre-#74 board produced, so adopting the layer changes no card position.
 * Returns a NEW array and never mutates the input or clones an element: the layer's memos and
 * `shared/dnd`'s item wrapper both key on element identity.
 */
export function boardOrder(deals: readonly CrmDeal[]): CrmDeal[] {
  const rank = (d: CrmDeal) => STAGE_RANK.get(d.stage) ?? STAGE_ORDER.length;
  return deals
    .map((deal, i) => ({ deal, i }))
    .sort(
      (a, b) =>
        rank(a.deal) - rank(b.deal) ||
        (b.deal.lead_score ?? -1) - (a.deal.lead_score ?? -1) ||
        a.i - b.i,
    )
    .map(x => x.deal);
}

/**
 * The stage columns the board renders, in STAGE_ORDER. Two independent mechanisms narrow it,
 * and they mean different things: `hiddenStages` is a persisted VISIBILITY preference (the
 * column is put away), while the stage facet is a FILTER (selecting stages also hides the
 * others, and Clear-filters restores them). A stage hidden by the preference stays hidden even
 * when the facet selects it — putting a column away is the stronger statement.
 */
export function visibleStageKeys(
  hiddenStages: ReadonlySet<string>,
  stageFacet: readonly (string | number)[],
): string[] {
  const shown = STAGE_ORDER.filter(s => !hiddenStages.has(s));
  return stageFacet.length === 0 ? shown : shown.filter(s => stageFacet.includes(s));
}

/**
 * Header figures over the deals the board can actually show. Open stages only, so the number
 * means "live pipeline" rather than "every row on screen" — `won`/`lost` value is booked or
 * gone, and summing it would inflate the figure the header calls "open".
 */
export function openPipelineTotals(deals: readonly CrmDeal[]): {
  openTotal: number;
  openCount: number;
} {
  let openTotal = 0;
  let openCount = 0;
  for (const d of deals) {
    if (OPEN_STAGES.includes(d.stage)) {
      openTotal += d.value || 0;
      openCount += 1;
    }
  }
  return { openTotal, openCount };
}

/**
 * Restore the hidden-stage preference. Tolerant of junk for the same reason
 * `pipelineFilters.loadFilterState` was: a corrupt key must not blank the board. Unknown stage
 * names are dropped — they could only hide nothing, but keeping them would let a stale key
 * accumulate forever.
 */
export function loadHiddenStages(): Set<string> {
  try {
    const raw = sessionStorage.getItem(HIDDEN_STAGES_KEY);
    if (!raw) return new Set();
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((s): s is string => typeof s === 'string' && STAGE_ORDER.includes(s)));
  } catch {
    return new Set();
  }
}

export function saveHiddenStages(hidden: ReadonlySet<string>): void {
  try {
    sessionStorage.setItem(HIDDEN_STAGES_KEY, JSON.stringify([...hidden]));
  } catch {
    /* private mode / quota — the preference is a convenience, never a correctness input */
  }
}

/**
 * The Won card's "last contact" line (issue #129).
 *
 * Before the close, a deal card carries nudges about whether it is still ALIVE — the lead score
 * and the AI touch count. After it, neither question is live any more and the board is read for a
 * different one: which accounts have gone quiet since we won them. So a Won card trades those two
 * for this.
 *
 * `last_activity_at` is #21's `MAX` over the deal's logged activities and its un-archived notes —
 * the same derivation the Contacts list already labels "Last contact", so the two surfaces cannot
 * tell different stories about the same word. Nothing new is queried: `get_pipeline()` has
 * returned this column since #21.
 *
 * A deal with nothing logged says so outright rather than rendering an empty slot. That follows
 * #128's rule that an absent value is a state worth reading, and here it is the strongest signal
 * on the board: a won account nobody has followed up on is exactly what this line exists to
 * surface, so hiding it would invert the feature.
 *
 * `now` is injectable so the label can be tested without the wall clock.
 */
export function lastContactLabel(
  lastActivityAt: string | null | undefined,
  now: Date = new Date(),
): string {
  if (!lastActivityAt) return 'No contact logged';
  const age = formatAge(lastActivityAt, now);
  // `formatAge` answers "today" for anything under a day, which reads wrong with "ago".
  return age === 'today' ? 'Last contact today' : `Last contact ${age} ago`;
}
