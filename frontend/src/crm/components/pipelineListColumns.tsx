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

/** Every sortable field except the array order, plus the display-only stage column. */
type PipelineColumnKey = Exclude<PipelineSortField, 'boardOrder'> | 'stage';

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
 * `last_activity_at` is a full TIMESTAMPTZ, where ordinary parsing (and conversion to the
 * viewer's zone) is exactly right.
 */
function shortDate(value: string | null | undefined): string {
  if (!value) return '—';
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  const d = dateOnly
    ? new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]))
    : new Date(value);
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
          <span style={{ color: INK, fontWeight: 500, opacity: isArchivedDeal(d) ? 0.65 : 1 }}>{d.title}</span>
        </span>
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
