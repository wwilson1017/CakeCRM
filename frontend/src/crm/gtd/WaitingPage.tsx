import { useMemo, useState } from 'react';
import { SearchFilterBar } from '../../shared/search';
import { listTodos } from './api';
import { RecordChip } from './components/RecordChip';
import { TodoEditSheet } from './components/TodoEditSheet';
import { useRowActions, useTodos } from './hooks';
import { EmptyState, LoadFailed, LoadingRows, TodoShell } from './TodoShell';
import type { Todo } from './types';
import { useTodoMeta } from './useTodoMeta';
import { formatAge, matchesFilter } from './util';

/**
 * Waiting-for + delegated, each row carrying its context and its age — the number
 * that tells you a follow-up is overdue. One-click reactivate to Next.
 *
 * These rows are hand-built rather than TodoRow (they carry the age chip and the
 * reactivate button instead of a done checkbox), which is why the context chip
 * TodoRow already renders has to be repeated here.
 *
 * Deliberately NOT a CollectionView surface: the page is two fixed sections fed by
 * two separate fetches, and the collection layer's list view has no sections
 * primitive.
 */
export function WaitingPage() {
  const { todos: waiting, failed: f1, reload: r1 } =
    useTodos(() => listTodos({ status: 'waiting_for', limit: 500 }));
  const { todos: delegated, failed: f2, reload: r2 } =
    useTodos(() => listTodos({ status: 'delegated', limit: 500 }));
  const reload = () => { r1(); r2(); };
  const { setStatus, after } = useRowActions(reload);
  const { projects, filters } = useTodoMeta();
  const [search, setSearch] = useState('');
  const [editTodo, setEditTodo] = useState<Todo | null>(null);

  const failed = f1 || f2;
  const loading = waiting === null || delegated === null;

  const memoWaiting = useMemo(
    () => (waiting ?? []).filter(t => matchesFilter(t, search)), [waiting, search]);
  const memoDelegated = useMemo(
    () => (delegated ?? []).filter(t => matchesFilter(t, search)), [delegated, search]);

  const section = (title: string, items: Todo[]) => (
    <section>
      <h2 className="mb-2 text-xs font-heading font-bold uppercase tracking-wide text-muted">
        {title} ({items.length})
      </h2>
      {items.length === 0 ? (
        <p className="text-sm text-muted">Nothing here.</p>
      ) : (
        <div className="space-y-2">
          {items.map(t => (
            <div key={t.id}
                 className="flex items-start gap-3 rounded-xl border border-line-faint bg-cream px-3 py-2.5">
              <button type="button" onClick={() => setEditTodo(t)} className="min-w-0 flex-1 text-left">
                {/* The title wraps in full; the notes stay a one-line preview, which
                    is what the age chip beside it is scanned against. */}
                <p className="text-sm break-words text-charcoal">{t.title}</p>
                {t.notes && <p className="truncate text-xs text-muted">{t.notes}</p>}
                {(t.deal_title || t.contact_name) && (
                  <p className="mt-0.5 text-xs"><RecordChip todo={t} /></p>
                )}
              </button>
              {t.context && (
                <span className="shrink-0 rounded-full bg-sand px-2 py-0.5 text-xs text-muted">
                  {t.context}
                </span>
              )}
              <span className="shrink-0 rounded-full bg-sand px-2 py-0.5 text-xs text-muted"
                    title="Time since last touched">
                {formatAge(t.updated_at)}
              </span>
              <button
                type="button"
                onClick={() => void setStatus(t, 'next_action')}
                className="shrink-0 rounded-lg border border-line px-2.5 py-1.5 text-xs font-heading text-charcoal hover:bg-sand"
                title="Reactivate as a next action"
              >
                → Next
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );

  const total = (waiting?.length ?? 0) + (delegated?.length ?? 0);
  const visible = memoWaiting.length + memoDelegated.length;

  return (
    <TodoShell active="waiting" onAdded={reload}>
      <SearchFilterBar
        query={search}
        onQueryChange={setSearch}
        placeholder="Filter…"
        visibleCount={visible}
        totalCount={total}
        countNoun="item"
        active={search.trim() !== ''}
        activeFacetCount={0}
        onClear={() => setSearch('')}
      />
      {failed && <LoadFailed retry={reload} />}
      {!failed && loading && <LoadingRows />}
      {!failed && !loading && total === 0 && (
        <EmptyState
          title="Nothing pending on anyone else"
          hint="Items you're waiting on, or have delegated, show up here with their age."
        />
      )}
      {!failed && !loading && total > 0 && (
        <div className="space-y-6">
          {section('Waiting for', memoWaiting)}
          {section('Delegated', memoDelegated)}
        </div>
      )}
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
