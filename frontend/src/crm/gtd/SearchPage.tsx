import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { toast } from '../../shared/toast';
import { listTodos } from './api';
import { ContextRename } from './components/ContextRename';
import { TodoEditSheet } from './components/TodoEditSheet';
import { TodoRow } from './components/TodoRow';
import { STATUS_META, TODO_STATUS_ORDER } from './constants';
import { useRowActions, useTodosChanged } from './hooks';
import { isTodoPublicMode } from './publicMode';
import { EmptyState, LoadFailed, LoadingRows, TodoShell } from './TodoShell';
import type { Todo, TodoStatus } from './types';
import { useTodoMeta } from './useTodoMeta';

const OPEN_STATUSES: TodoStatus[] = [
  'inbox', 'next_action', 'waiting_for', 'delegated', 'someday_maybe',
];

/**
 * Contexts browse + global search results. The query comes from ?q= (the
 * always-visible shell search box). With no query, every open todo is shown grouped
 * by context; with one, the whole list — finished included — is searched server-side
 * and grouped by status.
 */
export function SearchPage() {
  const [searchParams] = useSearchParams();
  const query = (searchParams.get('q') || '').trim();
  const [context, setContext] = useState('');
  const [todos, setTodos] = useState<Todo[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [editTodo, setEditTodo] = useState<Todo | null>(null);
  const { projects, filters, refreshMeta } = useTodoMeta();

  const [loadSeq, setLoadSeq] = useState(0);
  const reload = useCallback(() => { setLoadSeq(s => s + 1); }, []);
  // This page fetches its own list rather than going through `useTodos`, so it subscribes
  // to the undo broadcast itself (#231).
  useTodosChanged(reload);

  useEffect(() => {
    let cancelled = false;
    const fetchAll = query
      ? listTodos({ search: query, limit: 500 })
      : Promise.all(OPEN_STATUSES.map(s => listTodos({ status: s, limit: 500 })))
          .then(lists => lists.flat());
    fetchAll
      .then(results => {
        if (cancelled) return;
        setTodos(results);
        setFailed(false);
      })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [query, loadSeq]);

  const { toggleDone, toggleStar, after } = useRowActions(reload);

  // Rename a context across every todo (#280). The no-login web app has no rename route.
  const knownContexts = filters?.contexts ?? [];
  const canRename = (c: string) => !isTodoPublicMode && knownContexts.includes(c);
  const renamed = (from: string, to: string) => {
    if (context === from) setContext(to);
    toast.success(`Renamed "${from}" to "${to}".`);
    reload();
    void refreshMeta();
  };
  // A context only finished todos still carry has no group to hang the control on, so
  // the one chosen in the filter gets it beside the dropdown instead.
  const renameBesideFilter = canRename(context)
    && (!!query || !(todos ?? []).some(t => t.context === context));

  const visible = (todos ?? []).filter(
    t => !context || t.context.toLowerCase() === context.toLowerCase(),
  );

  const groups: { key: string; title: string; items: Todo[] }[] = [];
  if (query) {
    for (const s of TODO_STATUS_ORDER) {
      const items = visible.filter(t => t.status === s);
      if (items.length) groups.push({ key: s, title: STATUS_META[s].label, items });
    }
  } else {
    const contexts = [...new Set(visible.map(t => t.context || 'No context'))].sort();
    for (const c of contexts) {
      groups.push({
        key: c,
        title: c,
        items: visible.filter(t => (t.context || 'No context') === c),
      });
    }
  }

  return (
    <TodoShell active="search" onAdded={reload}>
      <div className="mb-4 flex items-center justify-between gap-2">
        <p className="text-sm text-muted">
          {query
            ? <>Results for <strong className="text-charcoal">“{query}”</strong></>
            : 'Browse by context'}
        </p>
        <select
          value={context}
          onChange={e => setContext(e.target.value)}
          className="max-w-44 rounded-lg border border-line bg-cream px-2 py-1.5 text-sm text-charcoal focus:border-brand focus:outline-none"
          aria-label="Filter by context"
        >
          <option value="">All contexts</option>
          {knownContexts.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>
      {renameBesideFilter && (
        <div className="mb-4 flex flex-wrap items-center gap-2 text-sm text-muted">
          <span>{context}</span>
          <ContextRename key={context} context={context} onRenamed={to => renamed(context, to)} />
        </div>
      )}
      {failed && <LoadFailed retry={reload} />}
      {!failed && todos === null && <LoadingRows />}
      {todos !== null && !failed && visible.length === 0 && (
        <EmptyState title={query ? 'No matches' : 'Nothing open'} />
      )}
      <div className="space-y-6">
        {groups.map(g => (
          <section key={g.key}>
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <h2 className="text-xs font-heading font-bold uppercase tracking-wide text-muted">
                {g.title} ({g.items.length})
              </h2>
              {!query && canRename(g.key) && (
                <ContextRename context={g.key} onRenamed={to => renamed(g.key, to)} />
              )}
            </div>
            <div className="space-y-2">
              {g.items.map(t => (
                <TodoRow key={t.id} todo={t} showStatus={!!query} onToggleDone={toggleDone}
                         onToggleStar={toggleStar} onEdit={setEditTodo} />
              ))}
            </div>
          </section>
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
