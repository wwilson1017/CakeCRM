/**
 * pipelineCollection — the pipeline's `CollectionConfig` (issue #74).
 *
 * One declarative object replaces what `PipelinePage` + `PipelineFilterBar` used to hand-wire:
 * search, five facets, sort, the two views, and all of their sessionStorage persistence. The
 * facet PREDICATES are unchanged — the two preset facets delegate to #21's `dealMatchesAdvanced`
 * rather than restating its rules, so the bucket definitions (`overdue` is open-deals-only,
 * `stale30` includes never-contacted) keep exactly one home.
 *
 * Named `pipelineCollection.ts` rather than `collectionConfig.ts` so it cannot collide with the
 * sibling list-page port (#77), which is adding a file by that name for Contacts/Companies/Tasks;
 * it follows the same `make*CollectionConfig(deps) → CollectionConfig<T>` shape, so folding the
 * two together later is a move, not a rewrite.
 *
 * TWO THINGS HERE ARE LOAD-BEARING AND EASY TO "TIDY" INTO BUGS:
 *
 *  1. The owner facet is declared UNCONDITIONALLY, even on a single-user install where the old
 *     filter bar hid it. `useUsers()` starts at `[]` on a cold cache and fills asynchronously,
 *     while `useCollectionState` coerces its persisted envelope exactly once, in a `useState`
 *     initialiser, over the facets the config declares AT THAT MOMENT — and then writes that
 *     envelope back. A facet that appears later therefore has its restored selection silently
 *     erased before the roster lands. A stable facet schema is worth one redundant group.
 *
 *  2. There is deliberately NO `getVoided`. Its absence is what makes `state.kanbanItems` and
 *     `state.visibleItems` the same array, which is the precondition for the bulk bar's count
 *     (computed by the layer from the current view) and the apply payload (recomputed by the
 *     page from `visibleItems`) being the same set. Adding one would split them silently.
 *     Since #83 archived deals CAN reach the board, so that is no longer a free absence — the
 *     Archived facet below carries them instead, as an ordinary `single` facet. The layer's
 *     voided tri-state was the other candidate and was declined: its resting value (`null`)
 *     means SHOW ALL where ours must mean live-only, so adopting it would have meant teaching
 *     the shared layer a per-config default plus per-config copy ("Voided" is not the word for
 *     an archived deal) — shared-layer design this port has no mandate to do. What the board
 *     needs beyond the facet, it reads from `isArchivedDeal` directly.
 */

import type { CollectionConfig, FacetDef } from '../shared/collection';
import type { FacetOption } from '../shared/search';
import type { ListColumn } from '../shared/listview';
import type { CrmDeal } from '../core/types';
import { DEAL_DETAIL_CONFIG } from './dealDetailConfig';
import type { CrmUser } from './useUsers';
import { UNASSIGNED_LABEL } from './useUsers';
import { STAGE_COLORS, STAGE_ORDER } from './constants';
import {
  EMPTY_ADVANCED,
  dealMatchesAdvanced,
  isArchivedDeal,
  type ActivityPreset,
  type ClosePreset,
} from './pipelineFilters';
import { stageLabel, stageToggleKey } from './pipelineBoard';
import { PIPELINE_DEFAULT_SORT, pipelineSortFields } from './pipelineSort';

/** Labels carried over verbatim from the retired PipelineFilterBar, so the facets read the same. */
export const CLOSE_OPTIONS: FacetOption[] = [
  { value: 'overdue', label: 'Overdue' },
  { value: 'next7', label: 'Next 7 days' },
  { value: 'thisMonth', label: 'This month' },
  { value: 'noDate', label: 'No close date' },
];

export const ACTIVITY_OPTIONS: FacetOption[] = [
  { value: 'le7', label: 'Active (≤ 7 days)' },
  { value: 'le30', label: 'Active (≤ 30 days)' },
  { value: 'stale30', label: 'No activity in 30+ days' },
  { value: 'none', label: 'No activity logged' },
];

/** Archived visibility (issue #83), carried over verbatim from the retired filter bar. Unlike
 *  every other facet this one also widens the server FETCH — `PipelinePage` keys
 *  `?include_archived=true` off it — because archived deals are swept out of the board payload
 *  and a client predicate cannot filter rows it never received. "Archived only" is the recovery
 *  view: the way back from an accidental archive on an install with no AI provider. */
export const ARCHIVED_OPTIONS: FacetOption[] = [
  { value: 'include', label: 'Include archived' },
  { value: 'only', label: 'Archived only' },
];

/** The two selections that widen the fetch. Exported so `PipelinePage` tests the SAME values
 *  the facet offers rather than re-typing the strings. */
export function archivedSelectionIncludesArchived(value: unknown): boolean {
  return value === 'include' || value === 'only';
}

export interface PipelineConfigDeps {
  /** The install roster (`useUsers().users`) — options only; the facet is always declared. */
  users: CrmUser[];
  /** `useUsers().nameFor` — resolves owner_id to a display name for the Owner sort field. */
  ownerName: (id: number | null | undefined) => string;
  listColumns: ListColumn<CrmDeal>[];
}

export function makePipelineCollectionConfig(deps: PipelineConfigDeps): CollectionConfig<CrmDeal, 'column'> {
  const facets: FacetDef<CrmDeal>[] = [
    {
      kind: 'multi',
      key: 'stage',
      label: 'Stage',
      getValue: d => d.stage,
      options: STAGE_ORDER.map(s => ({
        value: s,
        label: stageLabel(s),
        color: STAGE_COLORS[s]?.fill ?? null,
      })),
      hint: 'Selecting stages also hides the other columns.',
    },
    {
      kind: 'multi',
      key: 'owner',
      label: 'Owner',
      // `owner_id` absent and `owner_id: null` are the same real state — unassigned — so both
      // map to one bucket, matching #21's `matchesOwner`.
      getValue: d => d.owner_id ?? 'unassigned',
      options: [
        { value: 'unassigned', label: UNASSIGNED_LABEL },
        ...deps.users.map(u => ({
          value: u.id,
          // Inactive owners stay selectable: a departed rep still owns deals, and filtering to
          // them is exactly how you find the work that needs reassigning.
          label: (u.name.trim() || u.email) + (u.is_active ? '' : ' (deactivated)'),
        })),
      ],
    },
    {
      kind: 'range',
      key: 'value',
      label: 'Value',
      getValue: d => d.value ?? 0,
      format: n => `$${n.toLocaleString()}`,
    },
    {
      kind: 'single',
      key: 'closeDate',
      label: 'Close date',
      options: CLOSE_OPTIONS,
      // Delegates rather than restates: `dealMatchesAdvanced` owns what each bucket means.
      // The clock is read per call; a memo pass spanning exact midnight could therefore judge
      // two deals against different days — a sub-millisecond window, accepted and stated
      // rather than glossed (the pre-#74 board snapshotted one `now` per recompute).
      predicate: (d, v) =>
        dealMatchesAdvanced(d, { ...EMPTY_ADVANCED, closeDate: v as ClosePreset }, new Date()),
    },
    {
      kind: 'single',
      key: 'lastActivity',
      label: 'Deal activity',
      options: ACTIVITY_OPTIONS,
      predicate: (d, v) =>
        dealMatchesAdvanced(d, { ...EMPTY_ADVANCED, lastActivity: v as ActivityPreset }, new Date()),
    },
    {
      kind: 'single',
      key: 'archived',
      label: 'Archived',
      options: ARCHIVED_OPTIONS,
      // Selected ⇒ archived rows are wanted, so 'include' passes everything and 'only' keeps
      // just them. The `!isArchivedDeal` fallback covers a value that is neither — the layer's
      // scalar coercion accepts any string, and an unrecognised one must fail toward LIVE-ONLY
      // (the resting behaviour) rather than quietly widening the board.
      //
      // The resting state (`null` ⇒ inactive ⇒ this predicate never runs) is enforced by the
      // SERVER, not here: `get_pipeline`'s LIVE_PREDICATE, which is why the facet widens the
      // fetch at all. `PipelinePage.load` closes the one gap that leaves — a live-only refetch
      // that is deferred or fails while archived rows are still in `data`.
      predicate: (d, v) =>
        v === 'include' ? true : v === 'only' ? isArchivedDeal(d) : !isArchivedDeal(d),
    },
  ];

  return {
    storage: { key: 'crm_pipeline', version: 1 },
    defaultView: 'kanban',
    getItemId: d => d.id,
    // The same haystack the pre-#74 board searched. The layer upgrades HOW it matches
    // (accent-folding, token-AND, 250ms debounce) but not WHAT it searches.
    searchText: d => [d.title, d.contact_name, d.company_name],
    persistSearch: true,
    facets,
    sort: { fields: pipelineSortFields(deps.ownerName), defaultSort: PIPELINE_DEFAULT_SORT },
    // One toggle per stage — the board's visibility preference. The page owns the values and
    // their persistence and feeds them back via `controlledToggles`, because the alternative is
    // circular: a hidden stage's deals are filtered out of `items`, and `items` is an INPUT to
    // the hook whose `state.toggles` would otherwise be the source.
    toggles: STAGE_ORDER.map(s => ({ key: stageToggleKey(s), label: stageLabel(s), default: true })),
    kanban: {
      getColumnId: d => d.stage,
      // Every card in a column renders; the column body is its own scroller. The layer's default
      // 50-cap would hide most of a busy Lead column behind "Show N more" — and, under the
      // default drag policy, would lock drag board-wide for it.
      columnCap: Number.MAX_SAFE_INTEGER,
      // A drop assigns a stage and nothing else: deals carry no rank column, and
      // `handleKanbanMove` discards `newIndex`. So a filtered subset, a non-array sort or a
      // partially-rendered column cannot make a drop ambiguous, and drag stays live through all
      // three — which is the #21 behavior this port had to preserve, stated as a policy rather
      // than re-implemented.
      dragPolicy: 'column',
    },
    list: { columns: deps.listColumns },
    itemNoun: { singular: 'deal', plural: 'deals' },
    emptyState: { message: 'No deals to show.' },
    // The deal detail moved into the layer with #75. `DEAL_DETAIL_CONFIG` is the SAME object
    // `CrmDashboardPage` and `WeeklyTouchesDetailPage` hand `CollectionDetail`, so the three
    // hosts cannot drift about the title, the subtitle, or the `loadById` route that resolves an
    // off-board or archived deal for a shared `?deal=` link.
    detail: DEAL_DETAIL_CONFIG.detail,
    // Still no `getVoided` — see the header note, that absence is load-bearing.
  };
}
