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
import { parseUTC } from './gtd/util';

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const text = (v: string | null | undefined): string | null => (v ? v.toLowerCase() : null);
// Epoch ms for a TIMESTAMPTZ, via the same `parseUTC` the columns render through. (The original
// rationale — that JavaScriptCore rejects Postgres's six fractional digits — was measured false
// on WebKit 26.5; see the note in `pipelineListColumns.shortDate`. The reasons below stand.)
const instant = (v: string | null | undefined): number | null =>
  (v ? num(parseUTC(v).getTime()) : null);

const STATIC_SORT_FIELDS = [
  { value: 'boardOrder', label: 'Board order', arrayOrder: true },
  { value: 'title', label: 'Deal', get: (d: CrmDeal) => text(d.title) },
  { value: 'company', label: 'Company', get: (d: CrmDeal) => text(d.company_name || d.contact_name) },
  { value: 'value', label: 'Value', get: (d: CrmDeal) => num(d.value) },
  { value: 'probability', label: '% Closed', get: (d: CrmDeal) => num(d.probability) },
  { value: 'score', label: 'Lead score', get: (d: CrmDeal) => num(d.lead_score) },
  { value: 'touches', label: 'Touches', get: (d: CrmDeal) => num(d.ai_touch_count) },
  // A date-ONLY `YYYY-MM-DD` compares correctly with < / >, which is what sort.ts uses —
  // deliberately not localeCompare, whose ICU collation mis-orders ties.
  { value: 'closeDate', label: 'Close date', get: (d: CrmDeal) => d.expected_close_date || null },
  // `last_activity_at` does NOT get that treatment, and this reason is engine-independent: a
  // TIMESTAMPTZ is not lexicographically ordered, because the zone may be spelled `Z` or
  // `+00:00` and `Z` sorts after `+` — so the same instant written two ways compares unequal and
  // two rows can order by how their suffix happens to be punctuated. Sorting on the parsed
  // instant removes the question. Invalid input yields null rather than NaN, so it sinks to the
  // bottom in both directions under the null convention above instead of poisoning every
  // comparison it takes part in.
  { value: 'lastActivity', label: 'Last activity', get: (d: CrmDeal) => instant(d.last_activity_at) },
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
