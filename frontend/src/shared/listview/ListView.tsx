/**
 * Shared dense list view — the platform companion to
 * `shared/dnd`'s `KanbanBoard`.
 *
 * Renders already-loaded, already-filtered rows as a sortable table. It owns no
 * data fetching and no detail surface: `onRowClick` hands the item straight
 * back, so a consumer passes the SAME handler its kanban card already uses and
 * both views open the identical detail surface — including whatever that
 * surface later becomes — without this module importing it.
 *
 * Sorting is CONTROLLED: the consumer page owns the `SortState` so a search
 * bar's sort dropdown and these column headers stay two
 * affordances over one value rather than two competing mechanisms.
 */
import { useMemo, useState, type ReactNode } from 'react';
// `sortRows` adapts these columns onto the ONE shared comparator in `shared/search`, so a
// list view and the search bar's dropdown over the same page sort state can never order
// differently (the listview→search direction is the resolution an earlier rebase plan
// called for). `nextSortState` is the list view's own header cycle and
// stays local.
import { nextSortState } from './headerSort';
import sortRows from './sortRows';
import type { ListColumn, SortState } from './types';

/** Rows rendered before the cap kicks in. Matches a sibling surface's card
 *  database (`the blueprint's cards tab`), the closest relative:
 *  a flattened board can be far larger than any single column, and a sibling surface's
 *  `?all_closed=true` read is genuinely uncapped. */
const DEFAULT_RENDER_CAP = 300;

export interface ListViewProps<TItem extends { id: number | string }> {
  columns: ListColumn<TItem>[];
  /**
   * Flattened rows in natural BOARD order.
   *
   * Consumers must flatten by iterating their own ordered `columns`/stages
   * array — never `Object.entries(items)`, whose integer-like keys enumerate in
   * ascending NUMERIC order first and non-integer keys after, so row order
   * would silently depend on whether column ids happen to be numeric.
   */
  items: TItem[];
  /** Controlled sort. Pass `null` for natural order; omit `onSortChange` to
   *  render a non-interactive table. */
  sort?: SortState | null;
  onSortChange?: (next: SortState | null) => void;
  onRowClick: (item: TItem) => void;
  emptyMessage?: string;
  renderCap?: number;
  /** Extra classes per row — the collection layer strikes voided rows through here
   *  (`shared/corrections/voidedRowClass`, the standard: struck, never hidden). */
  rowClassName?: (item: TItem) => string;
}

/** Module scope, NOT nested in ListView: a component declared during render is
 *  a new function identity each time, so React reconciles it as a different
 *  element *type* and remounts the subtree — which destroys the focused node
 *. Lint cannot be relied on to catch it. Mirrors the same reasoning in
 *  `the blueprint's tracking-log table`'s `SortTh`. */
function ListHeaderCell({
  label,
  columnKey,
  align,
  className,
  sort,
  onSortChange,
  sortable,
}: {
  label: ReactNode;
  columnKey: string;
  align: 'left' | 'right';
  className: string;
  sort: SortState | null;
  onSortChange: ((next: SortState | null) => void) | undefined;
  sortable: boolean;
}) {
  const active = sort?.key === columnKey ? sort : null;
  const base = `px-4 py-2.5 text-xs font-semibold uppercase tracking-wider ${
    align === 'right' ? 'text-right' : 'text-left'
  } ${className}`;

  if (!sortable || !onSortChange) {
    return <th className={base}>{label}</th>;
  }

  return (
    <th
      className={base}
      // First aria-sort in the codebase — a sortable header that announces
      // nothing is invisible to a screen reader.
      aria-sort={active ? (active.dir === 'asc' ? 'ascending' : 'descending') : undefined}
    >
      <button
        type="button"
        onClick={() => onSortChange(nextSortState(sort, columnKey))}
        className={`inline-flex items-center gap-1 uppercase tracking-wider transition-colors ${
          align === 'right' ? 'flex-row-reverse' : ''
        } ${active ? 'text-white' : 'text-white/80 hover:text-white'}`}
      >
        {label}
        <span aria-hidden="true" className="text-[10px] leading-none">
          {active ? (active.dir === 'asc' ? '▲' : '▼') : '↕'}
        </span>
      </button>
    </th>
  );
}

export default function ListView<TItem extends { id: number | string }>({
  columns,
  items,
  sort = null,
  onSortChange,
  onRowClick,
  emptyMessage,
  renderCap = DEFAULT_RENDER_CAP,
  rowClassName,
}: ListViewProps<TItem>) {
  const [showAll, setShowAll] = useState(false);

  // A change in the SIZE of the result set puts the cap back in force — "show
  // all" was a decision about one specific result set, not a permanent mode.
  //
  // Keyed on the row COUNT, deliberately not on array identity. Every board here
  // re-derives its rows through a `useMemo`, so a refetch, a single-card patch,
  // or a rolled-back drag all mint a fresh array holding the same rows — and on
  // identity that would silently collapse an expanded 4,000-row list back to 300
  // and jump the page, with no filter having changed. Count is the cheap proxy
  // for "this is a different result set"; a filter that happens to preserve the
  // count keeps the expansion, which is the harmless direction to be wrong in.
  //
  // Done at render time rather than in an effect: a setState inside an effect
  // cascades a second render and is a build-blocking lint error under the React
  // Compiler ruleset (see the same note in `the blueprint's cards tab`, which
  // resolves it by resetting inside its filter handlers instead). This is
  // React's documented derived-state adjustment, with in-repo precedent in
  // CRM's own `PipelineTab.tsx`. It sets state only when the count
  // actually changes, so it can never loop.
  const [prevCount, setPrevCount] = useState(items.length);
  if (prevCount !== items.length) {
    setPrevCount(items.length);
    setShowAll(false);
  }

  // Extracted to `sortRows` in an earlier revision so a page can compute this exact order for its detail
  // panel's ‹ › navigation without re-deriving it — see that module's docstring.
  const sorted = useMemo(() => sortRows(columns, items, sort), [columns, items, sort]);

  const rows = showAll ? sorted : sorted.slice(0, renderCap);
  const truncated = sorted.length - rows.length;

  return (
    <>
      <div className="overflow-x-auto rounded-xl border border-line bg-cream">
        <table className="w-full">
          <thead>
            <tr className="bg-brand-maroon text-white">
              {columns.map(col => (
                <ListHeaderCell
                  key={col.key}
                  label={col.header}
                  columnKey={col.key}
                  align={col.align ?? 'left'}
                  className={col.className ?? ''}
                  sort={sort}
                  onSortChange={onSortChange}
                  sortable={col.sortValue !== undefined}
                />
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={columns.length} className="px-4 py-6 text-center text-sm text-muted">
                  {emptyMessage ?? 'Nothing to show.'}
                </td>
              </tr>
            )}
            {rows.map(item => (
              <tr
                key={item.id}
                onClick={() => onRowClick(item)}
                className={`cursor-pointer border-b border-line-faint hover:bg-sand ${rowClassName?.(item) ?? ''}`}
              >
                {columns.map(col => (
                  <td
                    key={col.key}
                    className={`px-4 py-1.5 text-xs text-charcoal ${
                      col.align === 'right' ? 'text-right tabular-nums' : ''
                    } ${col.className ?? ''}`}
                  >
                    {col.render(item)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {truncated > 0 && (
        <div className="mt-2 text-sm text-muted">
          Showing {rows.length} of {sorted.length} — refine the filters, or{' '}
          <button
            type="button"
            onClick={() => setShowAll(true)}
            className="text-ck-accent-text hover:underline"
          >
            show all
          </button>
          .
        </div>
      )}
    </>
  );
}
