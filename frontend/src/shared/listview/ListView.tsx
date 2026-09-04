/**
 * Shared dense list view — the platform companion to
 * `shared/dnd`'s `KanbanBoard`.
 *
 * Renders already-loaded, already-filtered rows as a sortable table. It owns no
 * data fetching and no detail surface: `onRowClick` hands the item straight
 * back, so a consumer passes the SAME handler its kanban card already uses and
 * both views open the identical detail surface — including whatever that
 * surface later becomes — without this module importing it. Since #148 the row
 * is keyboard-activatable (Enter/Space) whenever that handler is present, and
 * fully inert when it is not.
 *
 * Sorting is CONTROLLED: the consumer page owns the `SortState` so a search
 * bar's sort dropdown and these column headers stay two
 * affordances over one value rather than two competing mechanisms.
 */
import { useId, useMemo, useState, type ReactNode } from 'react';
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
  /**
   * Open the row's record. OPTIONAL since #148: omit it and rows render inert —
   * no pointer cursor, no hover, no tab stop, no key handler — because a focus
   * stop that does nothing is worse than none. A surface with nothing to open
   * must be able to say so, and the collection adapter's old
   * `onRowClick={row => onSelect?.(row.id)}` closure could not: it is always
   * truthy, so it minted a tab stop per row on a page that wired no handler.
   *
   * When present the row is a KEYBOARD stop: Enter and Space activate it, guarded
   * by `e.target === e.currentTarget` so a keystroke aimed at a control inside a
   * cell never also opens the row. The row's ARIA role is deliberately left alone
   * — `role="button"` would flatten interactive cell content out of the
   * accessibility tree (ARIA presentational children: the collection layer's
   * selection checkbox, a CRM list column's inline link or button) and would
   * orphan every cell's implicit `role="cell"`, which needs a `role="row"` parent.
   * Cells carrying their own interactive controls still stop CLICK propagation —
   * that contract is unchanged, and it is load-bearing for the KEYBOARD too: activating a
   * cell's `<button>` with Enter or Space makes the browser dispatch a `click` on it, which
   * bubbles here. The keydown guard cannot see that click, so a cell control that omits
   * `onClick={e => e.stopPropagation()}` opens the row on Enter as well as on a mouse click.
   * `ListView.test.tsx` pins both halves of that contract.
   */
  onRowClick?: (item: TItem) => void;
  emptyMessage?: string;
  renderCap?: number;
  /** Extra classes per row — the collection layer strikes voided rows through here
   *  (`shared/collection/voidedRowClass`, the standard: struck, never hidden). Its
   *  `voidedTableRowClass` variant is the one to use on THIS surface: since #148 the row
   *  owns a focus outline, and `opacity` on the `<tr>` would composite that outline down
   *  with the row. */
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
  // Per-instance: two ListViews can share a page, and a duplicated id would point both
  // tables' `aria-describedby` at the first one's hint.
  const hintId = useId();

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

  // simplification: a row that holds focus and is then unmounted — by the cap re-arming above,
  // or by a re-sort/refetch that drops it out of `rows` — sends focus to `<body>`, the browser
  // default. Harmless before #148 (nothing here was focusable); reachable now, though it needs a
  // list change to land while focus is parked on a row, and no CRM surface polls. Left as-is
  // deliberately: restoring focus needs a roving-tabindex/focus-restoration layer, and a partial
  // version (guessing a neighbour row) moves focus somewhere the user did not ask for, which is
  // worse than the documented default. Upgrade path: adopt that layer with `role="grid"` above.
  const rows = showAll ? sorted : sorted.slice(0, renderCap);
  const truncated = sorted.length - rows.length;

  return (
    <>
      <div className="overflow-x-auto rounded-xl border border-line bg-cream">
        {/* The rows are focusable but keep their `role="row"` (see `onRowClick`'s doc), so
            nothing in the accessibility tree announces that a row DOES anything — a residual
            WCAG 4.1.2 gap the keyboard fix alone does not close. Saying it once, as the table's
            DESCRIPTION, is the cheap honest fix: a screen reader reads it on entering the table,
            where a per-row hint would repeat itself up to `renderCap` (300) times.

            Deliberately `aria-describedby` and not a `<caption>`: per HTML-AAM a caption is the
            table's accessible NAME, so every list in the app would be *named* with this
            instruction — the same generic sentence in the tables rotor, and no table saying what
            it holds. An instruction is description material, not a name.

            Rendered only when rows actually open something AND there are rows, so neither an
            inert table nor an empty state ever claims otherwise.

            Two acknowledged ceilings, not oversights. (1) A table description is announced when a
            reader ENTERS the table, which a Tab-only user driving focus straight onto a row may
            not hear; delivering it per row instead would announce the same sentence on every one
            of up to `renderCap` rows, which is the worse failure. (2) A focusable `role="row"` is
            outside ARIA's defined table interaction model — the model that would cover it is
            `role="grid"`, which obliges full 2D arrow-key cell navigation and is a much larger
            change than this one. `role="button"` is the option that is simply wrong, for the
            reason in `onRowClick`'s doc. So a reader is told "row", not "opens this record".
            Upgrade path, and the only one that fully closes 4.1.2: give each column set a
            designated primary cell rendering a real `<a>`/`<button>` and move the tab stop
            onto that control, leaving the row itself unfocusable. That is a change to every
            adopting surface's columns rather than to this file, which is why it is not this
            fix — this one buys keyboard OPERABILITY for every adopter at once. */}
        {onRowClick && rows.length > 0 && (
          <p id={hintId} className="sr-only">
            Rows are interactive: focus a row and press Enter or Space to open its record.
          </p>
        )}
        <table
          className="w-full"
          aria-describedby={onRowClick && rows.length > 0 ? hintId : undefined}
        >
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
                onClick={onRowClick ? () => onRowClick(item) : undefined}
                onKeyDown={
                  onRowClick
                    ? e => {
                        // A keyboard event targets the FOCUSED element, so this guard
                        // reads "the row itself has focus". A keystroke aimed at a
                        // control inside a cell — the collection layer's selection
                        // checkbox, a CRM list column's inline link or button —
                        // bubbles here carrying its own target and must never also
                        // open the row (#148). The CLICK path is not guarded this
                        // way: a mouse click always targets a cell or its content,
                        // never the `<tr>`, so the same test there would swallow
                        // every row click. Click suppression stays the cell's own
                        // `stopPropagation`, exactly as before.
                        if (e.target !== e.currentTarget) return;
                        if (e.key !== 'Enter' && e.key !== ' ') return;
                        e.preventDefault(); // Space would otherwise scroll the page.
                        onRowClick(item);
                      }
                    : undefined
                }
                tabIndex={onRowClick ? 0 : undefined}
                // Focus is an OUTLINE, not a `ring`, and the native outline is deliberately
                // NOT suppressed. Two reasons, both measured against THIS theme (#148):
                // Tailwind's `ring-*` compiles to `box-shadow`, which WebKit does not paint on
                // a `display: table-row` element — so the usual `focus:outline-none
                // focus-visible:ring-…` idiom, correct on a button or a div, renders NOTHING on
                // a `<tr>` in Safari and every iOS browser, leaving a keyboard user with no
                // indicator at all. And `ring-brand/40` composites to ~#f4a5ae over `cream`,
                // ~1.9:1, under the 3:1 WCAG 1.4.11 asks of a focus indicator; full-strength
                // `brand` (= `--color-ck-accent`, #e31d3b, identical in both themes per #54) is
                // 4.7:1 on the light card and 3.2:1 on the dark one. `outline` paints on table
                // rows in every engine, and leaving the UA outline in place means even a failure
                // of these utilities degrades to a visible default rather than to nothing. The
                // negative offset draws it INSIDE the row so the container's `rounded-xl
                // overflow-x-auto` cannot clip it on the first or last row. No bare `outline`
                // class: that utility sets `outline-width: 1px` and, being emitted AFTER
                // `outline-2` in Tailwind's order, would silently override the 2px this asks
                // for. `outline-2` alone is sufficient — `--tw-outline-style` is declared with
                // `initial-value: solid`.
                className={`border-b border-line-faint ${
                  onRowClick
                    ? 'cursor-pointer hover:bg-sand focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-brand '
                    : ''
                }${rowClassName?.(item) ?? ''}`}
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
