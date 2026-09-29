/**
 * Todo GTD's CollectionConfigs (#234 — port of cake_os #1840's GTD half).
 *
 * Three of the GTD app's list pages go through the shared collection layer: **Someday**,
 * **Done** (flat record lists) and **Projects** (a card grid). The other three — Inbox, Next
 * Actions and Waiting — deliberately keep bespoke bodies and render `shared/search`'s
 * `SearchFilterBar` directly (see `contextFacet.ts`), because each depends on SECTIONS and the
 * collection layer's list view has no sections primitive: Inbox is a triage instrument (one
 * item promoted to a card, the rest a promote-only queue), Next Actions' context batching IS
 * that page's purpose, and Waiting is two fixed sections fed by two fetches. Flattening any of
 * them into a sort order would remove the thing the page exists for. If a list-view sections
 * primitive ever lands in `shared/collection`, those three become ordinary adoptions.
 *
 * JSX-free on purpose, like the CRM's `crm/collectionConfig.ts`: the one component cell is
 * built with `createElement`, so this file exports only factories and pure helpers.
 *
 * Someday and Done render ONE column whose cell is the existing `TodoRow` — it already carries
 * the checkbox, the star and the click-to-edit target, and `ListColumn.render` returns any
 * node, so behavior parity costs nothing. A multi-column table would be a redesign.
 *
 * Deliberate omissions across all three:
 *  • no `sort` — none of these pages has ever had a sort control, and the server order they
 *    rely on (newest-finished first on Done) is preserved by passing the array through.
 *  • no `detail` — Someday/Done open `TodoEditSheet` from inside the row, and a Projects card is
 *    a real `<Link>`. `CollectionView` mounts its modal only under `detail && config.detail &&
 *    onSelect`, so omitting both leaves those surfaces exactly as they were.
 *  • no `getVoided` — todos are dropped or completed, never voided.
 *  • no `persistSearch` — stays OFF; a silently pre-narrowed todo list reads as lost work.
 *
 * **Storage keys are constant, not owner-scoped** — a divergence from upstream, which keys each
 * list by the signed-in email or the public token. There, every person has their own todo list,
 * so an inherited context chip is someone else's vocabulary. Here the GTD store is install-wide
 * (docs/agents/todos-gtd.md: the GTD lists are deliberately unscoped), so every seat and every
 * `/todo` link looks at the SAME list and the SAME contexts; a chip left in the tab still means
 * what it said.
 */
import { createElement } from 'react';
import type { CollectionConfig } from '../../shared/collection';
import { TodoRow } from './components/TodoRow';
// The ONE context-option mapping, shared with the bespoke pages that render the bar directly.
import { contextOptions } from './contextFacet';
import type { Todo, TodoProject } from './types';

/**
 * The row affordances a Someday/Done cell needs. Each is a stable callback at the call site
 * (`useRowActions`' `useCallback`s, plus a `useState` setter for `onEdit`), which is what lets
 * the page memoize the factory result and meet the layer's referential-stability contract.
 */
export interface TodoRowActions {
  onToggleDone: (todo: Todo) => void;
  onToggleStar: (todo: Todo) => void;
  onEdit: (todo: Todo) => void;
}

/** Someday adds one action: commit the idea by moving it to next actions. */
export interface SomedayRowActions extends TodoRowActions {
  onPromote: (todo: Todo) => void;
}

/**
 * The free-text haystack, field-for-field identical to `util.matchesFilter`'s — pinned against
 * it in `collectionConfig.test.ts`. The layer tokenizes and ANDs terms where `matchesFilter` did
 * one substring test (a strict improvement), but the FIELDS must stay the same or a todo someone
 * used to find by its project name becomes unreachable.
 */
export function todoSearchText(t: Todo): (string | null | undefined)[] {
  return [t.title, t.notes, t.context, t.project_name, ...t.tags];
}

/**
 * The context facet both todo configs declare. Values are lower-cased on BOTH sides (options via
 * `contextOptions`, the getter here) because the layer matches facet values exactly while a
 * context is free text the user types in any case — the same rule `matchesContexts` applies on
 * the bespoke pages. `|| null` so a blank context is "no value" rather than a nameless chip that
 * matches every unfiled todo.
 */
function contextFacet(contexts: string[]) {
  return {
    kind: 'multi' as const,
    key: 'context',
    label: 'Context',
    getValue: (t: Todo) => t.context.toLowerCase() || null,
    options: contextOptions(contexts),
  };
}

function todoRow(actions: TodoRowActions, t: Todo) {
  return createElement(TodoRow, {
    todo: t,
    onToggleDone: actions.onToggleDone,
    onToggleStar: actions.onToggleStar,
    onEdit: actions.onEdit,
  });
}

/** One full-width column. `header: 'Todo'` rather than '' — `ListView` always renders its
 *  `<thead>`, and a blank one is a bare strip above the rows. */
function todoColumn(render: (t: Todo) => ReturnType<typeof createElement>) {
  return { key: 'todo', header: 'Todo', render };
}

/**
 * The layer shows `emptyState` only once the list has rows and a filter hid all of them: each
 * page renders its own `EmptyState` (with the GTD hint copy) for a list that is genuinely empty
 * and mounts `CollectionView` only when there is something to filter. So this message may say
 * "match" without ever lying to someone whose list is simply empty.
 */
const FILTERED_EMPTY = { message: 'No todos match your filter. Clear the search or filters above.' };

export function makeSomedayConfig(actions: SomedayRowActions, contexts: string[]): CollectionConfig<Todo> {
  return {
    storage: { key: 'todo_someday', version: 1 },
    defaultView: 'list',
    getItemId: t => t.id,
    searchText: todoSearchText,
    // Kept from the pre-#234 page, which already filtered Someday by context (upstream's
    // Someday was search-only; dropping the facet here would remove a working control).
    facets: [contextFacet(contexts)],
    list: {
      // The cell is the row plus its "→ Next" commit button, as the page rendered it before.
      columns: [todoColumn(t =>
        createElement('div', { className: 'flex items-start gap-2' },
          createElement('div', { className: 'min-w-0 flex-1' }, todoRow(actions, t)),
          createElement('button', {
            type: 'button',
            onClick: () => actions.onPromote(t),
            className: 'mt-1 shrink-0 rounded-lg border border-line px-2.5 py-1.5 text-xs font-heading text-charcoal hover:bg-sand',
            title: 'Commit to this — move it to next actions',
          }, '→ Next')))],
    },
    itemNoun: { singular: 'idea', plural: 'ideas' },
    emptyState: FILTERED_EMPTY,
  };
}

/**
 * One config serves both Done tabs and they share one storage key: the done|dropped toggle is a
 * FETCH key on the page (the server orders a growing list under a LIMIT), and a context filter
 * chosen on one tab carrying to the other is what the page did before.
 */
export function makeDoneConfig(actions: TodoRowActions, contexts: string[]): CollectionConfig<Todo> {
  return {
    storage: { key: 'todo_done', version: 1 },
    defaultView: 'list',
    getItemId: t => t.id,
    searchText: todoSearchText,
    facets: [contextFacet(contexts)],
    list: { columns: [todoColumn(t => todoRow(actions, t))] },
    itemNoun: { singular: 'todo', plural: 'todos' },
    emptyState: FILTERED_EMPTY,
  };
}

/**
 * Projects — a card grid whose cell the page owns end to end.
 *
 * A module constant: nothing here depends on runtime data. The card is supplied through
 * `CollectionCardsProps.renderCard` (`components/ProjectCard.tsx`) because it has two targets —
 * open the project, and complete/reactivate it — and the layer's built-in cell is one `<button>`.
 * `getTitle` is required by `CardsViewConfig` and unused under `renderCard`; it is still the
 * honest value. The status tabs stay a page-owned FETCH key, not a facet.
 */
export const projectsCollectionConfig: CollectionConfig<TodoProject> = {
  storage: { key: 'todo_projects', version: 1 },
  defaultView: 'cards',
  getItemId: p => p.id,
  searchText: p => [p.name, p.notes],
  cards: { getTitle: p => p.name },
  itemNoun: { singular: 'project', plural: 'projects' },
  emptyState: { message: 'No projects match your search. Clear it above.' },
};
