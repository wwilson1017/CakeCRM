import { useState } from 'react';
import { SearchFilterBar, toggleValue } from '../../shared/search';
import { listTodos } from './api';
import { QuickAdd } from './components/QuickAdd';
import { TodoEditSheet } from './components/TodoEditSheet';
import { TriageCard } from './components/TriageCard';
import { UndoPill } from './components/UndoPill';
import { contextGroup, matchesContexts } from './contextFacet';
import { useInboxFocus, useTodos } from './hooks';
import { EmptyState, FilterEmptyState, LoadFailed, LoadingRows, TodoShell } from './TodoShell';
import type { Todo } from './types';
import { refreshMeta, useTodoMeta } from './useTodoMeta';
import { matchesFilter } from './util';

/**
 * GTD triage — one item at a time. The head item gets the big card; the rest wait in
 * a compact list and can be promoted by clicking.
 *
 * Deliberately NOT a CollectionView surface: this is a triage instrument, not a
 * records list — one item is promoted to a full card and the remainder is a
 * promote-only queue, a shape no view in the collection layer can render. It uses the
 * shared SearchFilterBar directly instead (page-owns-state), so no hand-rolled filter
 * bar survives anywhere in the app.
 */
export function InboxPage() {
  const { todos, failed, reload } = useTodos(() => listTodos({ status: 'inbox', limit: 500 }));
  const { projects, filters } = useTodoMeta();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [editTodo, setEditTodo] = useState<Todo | null>(null);
  const [search, setSearch] = useState('');
  const [contexts, setContexts] = useState<(string | number)[]>([]);
  // Bumped only by an undo (below), and part of the card's key — so the restored item
  // always gets a FRESH card. A resolving write leaves `TriageCard` busy on purpose,
  // spent, counting on the reload to swap in a different head item and remount it; but
  // `useTodos` cancels a superseded fetch, so an undo clicked before the filing refetch
  // lands cancels that refetch, the list never drops the item, and the same
  // permanently-disabled card would still be mounted under the same id.
  const [focusSeq, setFocusSeq] = useState(0);

  // #231 — an undone filing puts the item back in the inbox, and it must come back as the
  // CURRENT triage card rather than be corrected silently somewhere down the queue.
  // Without this the item returns to the list but the card shows whatever `items[0]`
  // happens to be, so the user has no way to see the correction take effect.
  //
  // The filter bar is CLEARED with it, and that is the implementation of "show me this
  // item" rather than a side effect of it: `items` is filtered before `head` is picked,
  // so a restored item the bar excludes falls straight through to `items[0]` and the undo
  // looks like it did nothing. Reachable because `TodoEditSheet` files too, and THAT write
  // carries the title, notes and project — rename an item while filing it under a search
  // and undo puts it back wearing a title the search no longer matches. The user's last
  // action was Undo on a row they picked by name, so showing that row is what they asked
  // for; the query is one keystroke to retype and the item is on screen either way.
  useInboxFocus(id => {
    setSelectedId(id);
    setSearch('');
    setContexts([]);
    setFocusSeq(n => n + 1);
  });

  const items = (todos ?? []).filter(
    t => matchesContexts(t.context, contexts) && matchesFilter(t, search),
  );
  const head = items.find(t => t.id === selectedId) ?? items[0];
  const rest = items.filter(t => head && t.id !== head.id);

  // Two callbacks, because they mean different things to the selection. An in-place
  // edit (title, project, star) must NOT move you to another card — clearing the
  // selection there would bounce a manually-picked item back to the head of the list
  // mid-triage.
  const changed = () => {
    reload();
    void refreshMeta();
  };

  const processed = () => {
    setSelectedId(null);
    changed();
  };

  return (
    <TodoShell active="inbox" hideQuickAdd inlineUndo>
      <SearchFilterBar
        query={search}
        onQueryChange={setSearch}
        placeholder="Search inbox…"
        groups={[contextGroup(
          filters?.contexts ?? [],
          contexts,
          v => setContexts(prev => toggleValue(prev, v)),
        )]}
        visibleCount={items.length}
        totalCount={todos?.length ?? 0}
        countNoun="capture"
        active={search.trim() !== '' || contexts.length > 0}
        activeFacetCount={contexts.length}
        onClear={() => { setSearch(''); setContexts([]); }}
      />
      {/* Capture lives in the page body here, right above the card being triaged,
          rather than up in the shell above the filters — the shell's copy is
          suppressed for this page. */}
      <div className="mb-4">
        <QuickAdd onAdded={changed} />
      </div>
      {failed && <LoadFailed retry={reload} />}
      {!failed && todos === null && <LoadingRows />}
      {todos !== null && !failed && items.length === 0 && (
        (todos?.length ?? 0) > 0
          ? <FilterEmptyState />
          : <EmptyState title="Inbox zero" hint="Everything is captured and clarified. Mind like water." />
      )}
      {head && (
        /* Keyed by id: the card holds the chosen destination in local state until it is
           filed, and that choice belongs to ONE item. The undo counter rides along so a
           restored item never reuses the spent card it was filed from (see `focusSeq`). */
        <TriageCard
          key={`${head.id}:${focusSeq}`}
          todo={head}
          projects={projects}
          contexts={filters?.contexts ?? []}
          onProcessed={processed}
          onChanged={changed}
          onEdit={setEditTodo}
        />
      )}
      {/* The undo block (#265), in the page flow: right under the card — or under the
          empty state once the last item is filed — and above the queue, where the eye
          already is after filing. The shell's floating copy is off (`inlineUndo`). */}
      <UndoPill inline />
      {head && rest.length > 0 && (
        <div className="mt-4">
          <h2 className="mb-2 text-xs font-heading font-bold uppercase tracking-wide text-muted">
            {rest.length} more in inbox
          </h2>
          {/* Promote-only, deliberately. The whole row is one "triage this one
              next" target, so its title must never become a click-to-rename
              editor the way the card's is — an edit box opening mid-row makes
              skipping between items fiddly, and the item is about to land on the
              card where renaming belongs.
              `data-inbox-queue` is the test's handle on these rows: selecting
              them by a utility class would tie a test to a styling decision and
              break the moment the titles were made to wrap. */}
          <div className="space-y-1" data-inbox-queue>
            {rest.map(t => (
              <button
                key={t.id}
                type="button"
                onClick={() => setSelectedId(t.id)}
                // Wraps: choosing which capture to triage next is exactly the
                // moment you need to read the whole thing.
                className="block w-full break-words rounded-lg border border-line-faint bg-cream px-3 py-2 text-left text-sm text-charcoal hover:border-brand/40"
              >
                {t.title}
              </button>
            ))}
          </div>
        </div>
      )}
      {editTodo && (
        <TodoEditSheet
          todo={editTodo}
          projects={projects}
          contexts={filters?.contexts ?? []}
          onClose={() => setEditTodo(null)}
          onSaved={changed}
        />
      )}
    </TodoShell>
  );
}
