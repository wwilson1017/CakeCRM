/**
 * pipelineListColumns — the deal table behind the pipeline's List view (issue #74).
 *
 * Column `key`s deliberately equal the `pipelineSort.ts` field `value`s, because
 * `CollectionListView` derives each header's sort getter by matching the two: a column whose key
 * names a sort field is sortable and orders IDENTICALLY to the same choice in the toolbar's sort
 * dropdown, and one that doesn't (here, `stage`) simply renders. Typing `key` as
 * `PipelineColumnKey` makes a typo a compile error rather than a silently unsortable header.
 *
 * No column declares `sortValue` for the same reason — supplying one here would let the header
 * and the dropdown disagree about what "sort by Value" means.
 */

import type { ListColumn } from '../../shared/listview';
import type { CrmDeal } from '../../core/types';
import type { PipelineSortField } from '../pipelineSort';
import { stageLabel } from '../pipelineBoard';
import { STAGE_COLORS } from '../constants';
import { INK, INK_DIM, LINE_STRONG, mono } from '../../shared/styles';
import { ScorePill, TouchCountPill } from './badges';
import { isArchivedDeal } from '../pipelineFilters';
import { DealTemperatureCell } from './DealTemperatureCell';
import { parseUTC } from '../gtd/util';

/** Every sortable field except the array order, plus the two display-only columns.
 *
 *  `temperature` is display-only for the same reason `stage` is: neither names a
 *  `pipelineSort.ts` field, so `CollectionListView` renders it without a sort getter. Making
 *  it sortable would mean a new sort field, and a lexical sort over 'cold' | 'hot' | 'warm'
 *  orders the tiers wrongly while looking like it works — its own change, if it is wanted. */
type PipelineColumnKey = Exclude<PipelineSortField, 'boardOrder'> | 'stage' | 'temperature';

interface PipelineColumn extends Omit<ListColumn<CrmDeal>, 'key' | 'sortValue'> {
  key: PipelineColumnKey;
}

/**
 * The two date columns hold DIFFERENT kinds of value and must be parsed differently.
 *
 * `expected_close_date` is a date-only `YYYY-MM-DD` string that is ALREADY a local calendar
 * date. `new Date('2026-05-15')` reads it as UTC midnight, which renders as May 14 anywhere
 * west of UTC — the same off-by-a-day `pipelineFilters.ymd` exists to avoid on the filtering
 * side. So a date-only string is rebuilt from its calendar parts.
 *
 * `last_activity_at` is a full TIMESTAMPTZ, and it goes through `parseUTC` rather than the bare
 * constructor — but NOT for the reason this change was originally filed under, which the
 * evidence run disproved and which is worth recording so nobody re-derives it. Postgres returns
 * SIX fractional digits (the column is written from `datetime.now(timezone.utc).isoformat()`)
 * where ECMA-262 defines three, and the claim was that JavaScriptCore rejects the extra ones and
 * so this column showed "—" in Safari. Measured on WebKit 26.5 and the system `jsc`, it does
 * not: the bare constructor parses that string correctly, and the column was never broken there.
 *
 * What IS true, and is why the call stands: more than three digits is implementation-DEFINED
 * rather than guaranteed, so the bare constructor is a bet on engine behaviour the spec does not
 * require; and a zone-LESS timestamp is read as LOCAL by the constructor and as UTC by
 * `parseUTC`, which is a real divergence on every engine. `parseUTC` also matches what the GTD
 * surfaces already do with the same kind of value.
 */
function shortDate(value: string | null | undefined): string {
  if (!value) return '—';
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  const d = dateOnly
    ? new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]))
    : parseUTC(value);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString();
}

export function buildPipelineListColumns(
  ownerName: (id: number | null | undefined) => string,
): ListColumn<CrmDeal>[] {
  const columns: PipelineColumn[] = [
    {
      key: 'title',
      header: 'Deal',
      // The ARCHIVED marker rides the title cell rather than getting a column of its own: the
      // rows are only reachable with the Archived facet on, so a permanent column would be empty
      // almost always. The board labels its cards the same way (issue #83) and the two views
      // must not disagree about whether a row is workable.
      render: d => (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          {isArchivedDeal(d) && (
            <span style={{
              ...mono(9, INK_DIM), border: `1px solid ${LINE_STRONG}`, borderRadius: 3,
              padding: '1px 4px', flexShrink: 0,
            }}>ARCHIVED</span>
          )}
          {/* Dimmed by COLOUR, never by `opacity` — the same substitution the board card
              makes (issue #119). At 0.65 this measured 4.44:1 on a HOVERED row
              (`hover:bg-sand`, which the pipeline gets because it wires `onSelect`), under
              AA; `ink-dim` is 6.17:1 there. Keeping the two views on one treatment is also
              what the note above asks for. */}
          <span style={{ color: isArchivedDeal(d) ? INK_DIM : INK, fontWeight: 500 }}>{d.title}</span>
        </span>
      ),
    },
    {
      key: 'temperature',
      // A one-glyph column: a word header would be three times the width of its own content.
      // `ListColumn.header` types as ReactNode, so the accessible name rides an off-screen
      // span — the same shape as the Tasks list's icon-only Done column in crm/listColumns.tsx.
      header: <span className="sr-only">Temperature</span>,
      // Same control the board card renders — one temperature, one visual language, and one
      // place the cycle rules live (issue #125). The WRITER arrives by context rather than as
      // an argument, because this function is called from a `useMemo` that must stay stable and
      // may not hold a ref-reading callback; see `DealTemperatureCell` for the whole reason.
      render: d => (
        <DealTemperatureCell
          deal={d}
          // An archived deal is on the board to be found and restored, not worked — the same
          // reason its card cannot be dragged or bulk-selected (issue #83).
          disabled={isArchivedDeal(d)}
        />
      ),
    },
    {
      key: 'company',
      header: 'Company',
      className: 'hidden sm:table-cell',
      render: d => d.company_name || d.contact_name || '—',
    },
    {
      key: 'stage',
      header: 'Stage',
      render: d => (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              flexShrink: 0,
              background: STAGE_COLORS[d.stage]?.fill || INK_DIM,
            }}
          />
          {stageLabel(d.stage)}
        </span>
      ),
    },
    {
      key: 'value',
      header: 'Value',
      align: 'right',
      render: d => `$${(d.value || 0).toLocaleString()}`,
    },
    {
      key: 'probability',
      header: '% Closed',
      align: 'right',
      className: 'hidden md:table-cell',
      render: d => `${d.probability ?? 0}%`,
    },
    {
      key: 'score',
      header: 'Score',
      align: 'right',
      className: 'hidden md:table-cell',
      // Same pill the board card uses — one score, one visual language across both views.
      // ScorePill renders null for an unscored deal, leaving the cell empty.
      render: d => <ScorePill score={d.lead_score} compact />,
    },
    {
      key: 'touches',
      header: 'Touches',
      align: 'right',
      className: 'hidden lg:table-cell',
      // TouchCountPill renders null with no count, which is the zero-AI-keys degrade path.
      render: d => <TouchCountPill count={d.ai_touch_count} />,
    },
    {
      key: 'closeDate',
      header: 'Close date',
      className: 'hidden md:table-cell',
      render: d => shortDate(d.expected_close_date),
    },
    {
      key: 'lastActivity',
      header: 'Last activity',
      className: 'hidden lg:table-cell',
      render: d => shortDate(d.last_activity_at),
    },
    {
      key: 'owner',
      header: 'Owner',
      className: 'hidden lg:table-cell',
      // nameFor already yields "Unassigned" for a null owner.
      render: d => ownerName(d.owner_id),
    },
  ];
  return columns;
}
