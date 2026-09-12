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
 *
 * Since #124 that visibility has TWO keys, and they answer different questions. The
 * sessionStorage set (`crm_pipeline_hidden_stages`) is "which columns have I put away IN THIS
 * TAB right now" — transient, edited from the board. The localStorage boolean
 * (`cakecrm_pipeline_show_closed`) is "should a board START with `won`/`lost` showing" —
 * durable per device, edited from Settings, and FALSE by default, which is the whole of #124's
 * requirement. `loadHiddenStages` reads the second only when the first has nothing usable, so a
 * tab-local reveal outranks the default without overwriting it. One store could not express
 * both: collapsing them makes every board-side hide durable and every reveal permanent.
 */

import type { CrmDeal } from '../core/types';
import { CLOSED_STAGES, OPEN_STAGES, STAGE_ORDER } from './constants';
import { formatAge } from './gtd/util';

const STAGE_RANK = new Map(STAGE_ORDER.map((s, i) => [s, i]));

const HIDDEN_STAGES_KEY = 'crm_pipeline_hidden_stages';

/** The durable half — see the module docstring. Edited from Settings, read on every board mount. */
const SHOW_CLOSED_KEY = 'cakecrm_pipeline_show_closed';

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
 * Should a board START with the closed stages showing (#124)? Default FALSE — `won` and `lost`
 * are hidden — so an absent key, a first visit and blocked storage all degrade to the behaviour
 * the issue asks for rather than to the pre-#124 one.
 *
 * localStorage, not a server column: there is no per-user preferences store in this repo, and
 * `cakecrm_theme` is the sanctioned precedent for a personal display preference (including the
 * `cakecrm_` prefix — the sessionStorage keys on this page use `crm_`). Per device is accepted.
 */
export function loadShowClosedStages(): boolean {
  try {
    return localStorage.getItem(SHOW_CLOSED_KEY) === 'true';
  } catch {
    return false;
  }
}

/**
 * Write the preference AND bring the current tab's stored set into line.
 *
 * The reconciliation is not tidiness, it is the feature. `PipelinePage` persists its hidden set
 * on the first render, so by the time anyone walks from the board to Settings their tab ALWAYS
 * has a stored set — and `loadHiddenStages` honours a stored set over this preference, which is
 * exactly what makes a tab-local reveal possible. Writing only the preference would therefore
 * leave the setting looking INERT to the one person most likely to check it: the one who just
 * came from the board.
 *
 * Narrow on purpose. It touches `CLOSED_STAGES` and nothing else, so a manually hidden open
 * stage survives; and it applies the `show` ARGUMENT rather than re-reading the preference, so
 * the toggle still works for this tab when the localStorage write was refused.
 */
export function saveShowClosedStages(show: boolean): void {
  try {
    localStorage.setItem(SHOW_CLOSED_KEY, String(show));
  } catch {
    /* private mode / quota — this tab still follows the click, it just won't be remembered */
  }
  const next = new Set(loadHiddenStages());
  for (const stage of CLOSED_STAGES) {
    if (show) next.delete(stage);
    else next.add(stage);
  }
  saveHiddenStages(next);
}

/** What a tab with no usable stored set starts from — the durable preference, resolved. */
function defaultHiddenStages(): Set<string> {
  return loadShowClosedStages() ? new Set() : new Set(CLOSED_STAGES);
}

/**
 * Restore the hidden-stage preference for THIS TAB. Tolerant of junk for the same reason
 * `pipelineFilters.loadFilterState` was: a corrupt key must not blank the board. Unknown stage
 * names are dropped — they could only hide nothing, but keeping them would let a stale key
 * accumulate forever.
 *
 * With nothing usable stored the board falls back to the durable per-device default (#124).
 * A stored value is honoured verbatim, INCLUDING an empty array: that is a real state — "Show
 * all" was pressed — and re-seeding it would undo that button on the very next mount.
 */
export function loadHiddenStages(): Set<string> {
  try {
    const raw = sessionStorage.getItem(HIDDEN_STAGES_KEY);
    if (!raw) return defaultHiddenStages();
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return defaultHiddenStages();
    return new Set(parsed.filter((s): s is string => typeof s === 'string' && STAGE_ORDER.includes(s)));
  } catch {
    return defaultHiddenStages();
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

/**
 * How much room a board column gets, and how much a card in it says (issue #182).
 *
 * Three tiers off ONE input: how many stage columns the board is actually rendering. That is
 * `visibleStageKeys` above, and it is deliberately not a measured pixel width. A rep on a 5K
 * display and a rep on a laptop who have both narrowed the board to Proposal and Negotiation
 * are doing the same thing, and should see the same card — density keyed off measurement would
 * make it a property of their monitor instead of a property of their focus. It also keeps this
 * function pure, so the tier boundaries are unit-testable with no DOM and no ResizeObserver.
 *
 * `minWidth` is 288 at EVERY tier — today's fixed desktop column — so nothing ever gets narrower
 * than it is now and the 5-and-6-column default board is byte-identical to before. Only the
 * ceiling moves. The board's single scroll region (#129) still scrolls sideways once the mins
 * overflow, because `min-width` floors flex shrinking.
 *
 * The ceiling exists because "fill the row" and "readable" diverge past a point: two columns on
 * a wide monitor would otherwise be 700px each, which is a worse card than a 560px one beside
 * empty space. It rises with the tier because the tier is also adding fields — width and density
 * are one behaviour, not two that happen to correlate.
 *
 * A count of 0 (every stage hidden, or a facet matching none) lands in the widest tier and is
 * harmless: the board renders no columns at all, and `PipelinePage` shows `EmptyFilterState`
 * instead.
 */
export type BoardDensity = 'compact' | 'roomy' | 'wide';

export interface BoardColumnLayout {
  density: BoardDensity;
  /** Flex floor, px. Never below today's fixed column, so no tier is a narrowing. */
  minWidth: number;
  /** Flex ceiling, px. */
  maxWidth: number;
}

/** Today's fixed desktop column width, and the floor for every tier. */
const COLUMN_MIN_WIDTH_PX = 288;

export function boardColumnLayout(visibleStageCount: number): BoardColumnLayout {
  if (visibleStageCount <= 2) return { density: 'wide', minWidth: COLUMN_MIN_WIDTH_PX, maxWidth: 560 };
  if (visibleStageCount <= 4) return { density: 'roomy', minWidth: COLUMN_MIN_WIDTH_PX, maxWidth: 440 };
  return { density: 'compact', minWidth: COLUMN_MIN_WIDTH_PX, maxWidth: 360 };
}
