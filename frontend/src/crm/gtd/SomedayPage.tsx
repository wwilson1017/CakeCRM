import { useMemo, useState } from 'react';
import { SearchFilterBar, toggleValue } from '../../shared/search';
import { listTodos } from './api';
import { TodoEditSheet } from './components/TodoEditSheet';
import { TodoRow } from './components/TodoRow';
import { contextGroup, matchesContexts } from './contextFacet';
import { useRowActions, useTodos } from './hooks';
import { EmptyState, FilterEmptyState, LoadFailed, LoadingRows, TodoShell } from './TodoShell';
import type { Todo } from './types';
import { useTodoMeta } from './useTodoMeta';
import { matchesFilter } from './util';

/** Someday / Maybe — the parking lot the weekly review prunes. */
export function SomedayPage() {
  const { todos, failed, reload } = useTodos(() => listTodos({ status: 'someday_maybe', limit: 500 }));
  const { toggleDone, toggleStar, setStatus, after } = useRowActions(reload);
  const { projects, filters } = useTodoMeta();
  const [search, setSearch] = useState('');
  const [contexts, setContexts] = useState<(string | number)[]>([]);
  const [editTodo, setEditTodo] = useState<Todo | null>(null);

  const visible = useMemo(
    () => (todos ?? []).filter(
      t => matchesContexts(t.context, contexts) && matchesFilter(t, search),
    ),
    [todos, search, contexts],
  );

  return (
    <TodoShell active="someday" onAdded={reload}>
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
        countNoun="idea"
        active={search.trim() !== '' || contexts.length > 0}
        activeFacetCount={contexts.length}
        onClear={() => { setSearch(''); setContexts([]); }}
      />
      {failed && <LoadFailed retry={reload} />}
      {!failed && todos === null && <LoadingRows />}
      {todos !== null && !failed && visible.length === 0 && (
        (todos?.length ?? 0) > 0
          ? <FilterEmptyState />
          : <EmptyState title="Nothing on the someday list" hint="Ideas you're not committing to yet live here." />
      )}
      <div className="space-y-2">
        {visible.map(t => (
          <div key={t.id} className="flex items-start gap-2">
            <div className="min-w-0 flex-1">
              <TodoRow todo={t} onToggleDone={toggleDone}
                       onToggleStar={toggleStar} onEdit={setEditTodo} />
            </div>
            <button
              type="button"
              onClick={() => void setStatus(t, 'next_action')}
              className="mt-1 shrink-0 rounded-lg border border-line px-2.5 py-1.5 text-xs font-heading text-charcoal hover:bg-sand"
              title="Commit to this — move it to next actions"
            >
              → Next
            </button>
          </div>
        ))}
      </div>
      {editTodo && (
        <TodoEditSheet
          todo={editTodo}
          projects={projects}
          contexts={filters?.contexts ?? []}
          onClose={() => setEditTodo(null)}
          onSaved={after}
        />
      )}
    </TodoShell>
  );
}
