/**
 * Shared list-view types.
 *
 * The platform companion to `shared/dnd`: every kanban board can offer the same
 * records as a dense, sortable table. A list view is a pure flatten-and-render
 * over the `columns` + `items` data a board consumer already computes
 * client-side, so this module deliberately imports NOTHING from `shared/dnd`.
 */
import type { ReactNode } from 'react';

export type SortDir = 'asc' | 'desc';

/**
 * One sort selection.
 *
 * The CONSUMER PAGE owns this state — neither this module nor any search bar
 * does. A list view's column headers and (per the contract) a search
 * bar's sort dropdown are both *controlled* affordances over the same value,
 * so a board can never end up with two competing sort mechanisms.
 *
 * `null` (in place of a `SortState`) means natural / board order.
 */
export interface SortState {
  key: string;
  dir: SortDir;
}

/**
 * The value a row sorts by for a given column.
 *
 * `null`/`undefined` means "no value" and sorts to the BOTTOM in both
 * directions. A computed `0` or `''` is a real value — it is the column's
 * `sortValue` accessor that decides which is which.
 */
export type SortValue = number | string | null | undefined;

export interface ListColumn<TItem> {
  /** Stable identity, and the `SortState.key` this column controls. */
  key: string;
  /** Usually a string; a node is allowed for non-text headers (the collection layer's
   *  select-all checkbox column). */
  header: ReactNode;
  render: (item: TItem) => ReactNode;
  /** Present ⇒ the column is sortable. Omit for a display-only column. */
  sortValue?: (item: TItem) => SortValue;
  /**
   * Responsive visibility applied to BOTH the `<th>` and every `<td>`, so a
   * hidden column can never desynchronise header and body — e.g.
   * `'hidden md:table-cell'` (the house pattern from `crm/ContactsTab`).
   */
  className?: string;
  /** `'right'` for numeric columns — adds `text-right tabular-nums`. */
  align?: 'left' | 'right';
}

export type ViewMode = 'kanban' | 'list';
