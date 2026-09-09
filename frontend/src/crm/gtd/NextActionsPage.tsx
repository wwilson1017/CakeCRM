import { useMemo, useState } from 'react';
import { SearchFilterBar, toggleValue } from '../../shared/search';
import { listTodos } from './api';
import { TodoEditSheet } from './components/TodoEditSheet';
import { TodoRow } from './components/TodoRow';
import { contextGroup, matchesContexts } from './contextFacet';
import { useRowActions, useTodos } from './hooks';
import { EmptyState, LoadFailed, LoadingRows, TodoShell } from './TodoShell';
import type { Todo } from './types';
import { useTodoMeta } from './useTodoMeta';
import { matchesFilter } from './util';

const NO_CONTEXT = 'No context';

/**
 * Next actions: the starred handful pinned on top, then grouped by context for
 * batching (@calls together, @errands together).
 *
 * Deliberately NOT a CollectionView surface: that context grouping IS the page's
 * purpose, and the collection layer's list view is a flat table with no sections
 * primitive — flattening the groups into a sort order would quietly remove the
 * batching this page exists for.
 */
export function NextActionsPage() {
  const { todos, failed, reload } = useTodos(() => listTodos({ status: 'next_action', limit: 500 }));
  const { toggleDone, toggleStar, after } = useRowActions(reload);
  const { projects, filters } = useTodoMeta();
  const [search, setSearch] = useState('');
  const [contexts, setContexts] = useState<(string | number)[]>([]);
  const [editTodo, setEditTodo] = useState<Todo | null>(null);
  const [adding, setAdding] = useState(false);

  const visible = useMemo(
    () => (todos ?? []).filter(
      t => matchesContexts(t.context, contexts) && matchesFilter(t, search),
    ),
    [todos, search, contexts],
  );

  const starred = visible.filter(t => t.star);
  const groups = useMemo(() => {
    const byContext = new Map<string, Todo[]>();
    for (const t of visible.filter(v => !v.star)) {
      const key = t.context || NO_CONTEXT;
      byContext.set(key, [...(byContext.get(key) ?? []), t]);
    }
    return [...byContext.entries()].sort(([a], [b]) => {
      if (a === NO_CONTEXT) return 1;
      if (b === NO_CONTEXT) return -1;
      return a.localeCompare(b);
    });
  }, [visible]);

  const rows = (items: Todo[]) => (
    <div className="space-y-2">
      {items.map(t => (
        <TodoRow key={t.id} todo={t} onToggleDone={toggleDone}
                 onToggleStar={toggleStar} onEdit={setEditTodo} />
      ))}
    </div>
  );

  return (
    <TodoShell active="next" onAdded={reload}>
      <div className="mb-3 flex items-center justify-between gap-2">
        <p className="text-sm text-muted">
          {visible.length} next action{visible.length === 1 ? '' : 's'}
        </p>
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="rounded-lg bg-brand-dark px-3 py-1.5 text-sm font-heading text-white hover:bg-brand-deep"
        >
          Add next action
        </button>
      </div>
      <SearchFilterBar
        query={search}
        onQueryChange={setSearch}
        placeholder="Filter…"
        groups={[contextGroup(
          filters?.contexts ?? [],
          contexts,
          v => setContexts(prev => toggleValue(prev, v)),
        )]}
        visibleCount={visible.length}
        totalCount={todos?.length ?? 0}
        countNoun="next action"
        active={search.trim() !== '' || contexts.length > 0}
        activeFacetCount={contexts.length}
        onClear={() => { setSearch(''); setContexts([]); }}
      />
      {failed && <LoadFailed retry={reload} />}
      {!failed && todos === null && <LoadingRows />}
      {todos !== null && !failed && visible.length === 0 && (
        <EmptyState title="No next actions" hint="Triage your inbox, or add one above." />
      )}
      <div className="space-y-6">
        {starred.length > 0 && (
          <section>
            <h2 className="mb-2 text-xs font-heading font-bold uppercase tracking-wide text-ck-amber-text">
              ★ Starred
            </h2>
            {rows(starred)}
          </section>
        )}
        {groups.map(([context, items]) => (
          <section key={context}>
            <h2 className="mb-2 text-xs font-heading font-bold uppercase tracking-wide text-muted">
              {context}
            </h2>
            {rows(items)}
          </section>
        ))}
      </div>
      {(editTodo || adding) && (
        <TodoEditSheet
          todo={editTodo}
          defaults={{ status: 'next_action' }}
          projects={projects}
          contexts={filters?.contexts ?? []}
          onClose={() => { setEditTodo(null); setAdding(false); }}
          onSaved={after}
        />
      )}
    </TodoShell>
  );
}
