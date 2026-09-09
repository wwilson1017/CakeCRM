/**
 * Flatten the CURRENT view's visible order for ‹ › detail navigation.
 *
 * Pure and view-aware: prev/next must walk the order the user is LOOKING at, not the
 * canonical array — a list sorted by value navigates by value, a board navigates
 * column-major in the app's own column order. An item outside the visible set (voided under
 * 'hide', filtered out after opening) has no position here; `CollectionDetail` disables both
 * arrows rather than guessing.
 *
 * Render bounds are deliberately NOT applied: list pagination, kanban column caps, and cards
 * section caps limit what is RENDERED, not what the user is browsing — in detail mode ‹ ›
 * walks the whole filtered/sorted set (the Gmail model), the same way the list order here is
 * page-independent. Only the FILTERED set bounds navigation.
 */
import type { CollectionConfig, CollectionViewKind } from './types';

/** Exactly the config this module reads — it never touches `detail`, so it asks for less than
 *  `DetailHostConfig`. A full `CollectionConfig` satisfies it structurally. */
type VisibleOrderConfig<T> = Pick<CollectionConfig<T>, 'getItemId' | 'kanban' | 'cards'>;

export default function visibleOrder<T>(
  view: CollectionViewKind,
  config: VisibleOrderConfig<T>,
  visibleItems: readonly T[],
  kanbanItems: readonly T[],
  /** The app's ordered column ids (its `columns` prop order) — REQUIRED for kanban flattening;
   *  `Object.entries` grouping is banned for the same reason ListView's docstring bans it
   *  (integer-like keys enumerate numerically first, so order would depend on id shape). */
  columnIds: readonly (string | number)[] = [],
): (string | number)[] {
  switch (view) {
    case 'kanban': {
      const kanban = config.kanban;
      if (!kanban) return [];
      const byColumn = new Map<string | number, (string | number)[]>();
      for (const item of kanbanItems) {
        const col = kanban.getColumnId(item);
        const list = byColumn.get(col);
        if (list) list.push(config.getItemId(item));
        else byColumn.set(col, [config.getItemId(item)]);
      }
      const out: (string | number)[] = [];
      for (const col of columnIds) out.push(...(byColumn.get(col) ?? []));
      return out;
    }
    case 'cards': {
      const cards = config.cards;
      if (!cards?.getSection) return visibleItems.map(config.getItemId);
      // Section-major, sections in first-appearance order (the grid renders them that way).
      const bySection = new Map<string, (string | number)[]>();
      for (const item of visibleItems) {
        const section = cards.getSection(item);
        const list = bySection.get(section);
        if (list) list.push(config.getItemId(item));
        else bySection.set(section, [config.getItemId(item)]);
      }
      return [...bySection.values()].flat();
    }
    case 'list':
    default:
      return visibleItems.map(config.getItemId);
  }
}
