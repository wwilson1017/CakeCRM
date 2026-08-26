/**
 * pipelineSort — the sort fields the pipeline offers (issue #74).
 *
 * The `value` strings are a PERSISTED WIRE FORMAT: `useCollectionState` saves the active sort
 * under `collection_crm_pipeline_sort_v1`, so renaming a `value` silently resets sort for every
 * returning user, while a `label` may change freely.
 *
 * `boardOrder` is declared `arrayOrder: true` and is the resting sort. That is what keeps
 * drag legal at rest and makes the list read "natural" (no header arrow) rather than claiming a
 * sort it isn't applying: the page hands the layer an `items` array already in board order
 * (`pipelineBoard.boardOrder`), so array order IS the shipped stage-major / lead-score order.
 *
 * Every getter returns `null` — never `0` or `''` — for an absent value, because
 * `shared/search/sort.ts` sinks nulls to the bottom in BOTH directions while `0` is a real
 * number that would sort to the top ascending, putting valueless deals above real ones.
 */

import type { SortFieldDef, SortState } from '../shared/search';
import type { CrmDeal } from '../core/types';

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const text = (v: string | null | undefined): string | null => (v ? v.toLowerCase() : null);

const STATIC_SORT_FIELDS = [
  { value: 'boardOrder', label: 'Board order', arrayOrder: true },
  { value: 'title', label: 'Deal', get: (d: CrmDeal) => text(d.title) },
  { value: 'company', label: 'Company', get: (d: CrmDeal) => text(d.company_name || d.contact_name) },
  { value: 'value', label: 'Value', get: (d: CrmDeal) => num(d.value) },
  { value: 'probability', label: '% Closed', get: (d: CrmDeal) => num(d.probability) },
  { value: 'score', label: 'Lead score', get: (d: CrmDeal) => num(d.lead_score) },
  { value: 'touches', label: 'Touches', get: (d: CrmDeal) => num(d.ai_touch_count) },
  // ISO-8601 strings compare correctly with < / >, which is what sort.ts uses — deliberately
  // not localeCompare, whose ICU collation mis-orders timestamp ties.
  { value: 'closeDate', label: 'Close date', get: (d: CrmDeal) => d.expected_close_date || null },
  { value: 'lastActivity', label: 'Last activity', get: (d: CrmDeal) => d.last_activity_at || null },
] as const satisfies readonly SortFieldDef<CrmDeal>[];

/**
 * Owner sorts by the RESOLVED name, not `owner_id` — the id is an FK whose numeric order means
 * nothing to a rep — so the field table is a factory over the roster lookup rather than a
 * constant. Unassigned deals sort last in both directions via the null convention above.
 */
export function pipelineSortFields(
  ownerName: (id: number | null | undefined) => string,
): readonly SortFieldDef<CrmDeal>[] {
  return [
    ...STATIC_SORT_FIELDS,
    {
      value: 'owner',
      label: 'Owner',
      get: (d: CrmDeal) => (d.owner_id == null ? null : text(ownerName(d.owner_id))),
    },
  ];
}

/** Column keys the list view may offer a header sort on (every field except the array order). */
export type PipelineSortField =
  | (typeof STATIC_SORT_FIELDS)[number]['value']
  | 'owner';

export const PIPELINE_DEFAULT_SORT: SortState = { field: 'boardOrder', dir: 'asc' };
