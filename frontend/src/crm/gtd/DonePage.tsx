import { useMemo, useState } from 'react';
import { CollectionView, useCollectionState } from '../../shared/collection';
import { listTodos } from './api';
import { makeDoneConfig, useStableContexts } from './collectionConfig';
import { TodoEditSheet } from './components/TodoEditSheet';
import { useRowActions, useTodos } from './hooks';
import { isTodoPublicMode } from './publicMode';
import { EmptyState, LoadFailed, LoadingRows, TodoShell } from './TodoShell';
import type { Todo, TodoStatus } from './types';
import { useTodoMeta } from './useTodoMeta';

const NO_TODOS: Todo[] = [];

/**
 * Completed and dropped work, newest first.
 *
 * The status toggle is the fetch key, not a client-side filter: these lists grow
 * forever, so the server orders them newest-finished-first under a LIMIT and a
 * client-side split would show whatever happened to be in the window. Search and the
 * context facet run through the shared collection layer (#234) over whatever that
 * fetch returned; the config declares no sort, so the server's order passes through.
 */
export function DonePage() {
  const [status, setStatus] = useState<TodoStatus>('done');
  const { todos, failed, reload } = useTodos(
    () => listTodos({ status, limit: 200 }), status,
  );
  const { toggleDone, toggleStar, after } = useRowActions(reload);
  const { projects, filters } = useTodoMeta();
  const [editTodo, setEditTodo] = useState<Todo | null>(null);
  const contexts = useStableContexts(filters?.contexts);

  const config = useMemo(
    () => makeDoneConfig({ onToggleDone: toggleDone, onToggleStar: toggleStar, onEdit: setEditTodo }, contexts),
    [toggleDone, toggleStar, contexts],
  );
  const items = todos ?? NO_TODOS;
  const state = useCollectionState(config, items);

  const tab = (value: TodoStatus, label: string) => (
    <button
      key={value}
      type="button"
      onClick={() => setStatus(value)}
      aria-pressed={status === value}
      className={`rounded-lg px-3 py-1.5 text-sm font-heading transition-colors ${
        status === value
          ? 'bg-brand-dark text-white'
          : 'border border-line bg-cream text-muted hover:bg-sand'
      }`}
    >
      {label}
    </button>
  );

  return (
    <TodoShell active="done" onAdded={reload}>
      <div className="mb-3 flex gap-2">
        {tab('done', 'Done')}
        {tab('dropped', 'Dropped')}
      </div>
      {failed && <LoadFailed retry={reload} />}
      {!failed && todos === null && <LoadingRows />}
      {!failed && todos !== null && todos.length === 0 && (
        <EmptyState
          title={status === 'done' ? 'Nothing completed yet' : 'Nothing dropped'}
          hint={status === 'done'
            ? 'Finished work lands here, newest first.'
            : 'Dropping keeps an item out of your lists without deleting it.'}
        />
      )}
      {!failed && items.length > 0 && (
        <CollectionView
          config={config}
          state={state}
          items={items}
          searchPlaceholder="Search finished todos…"
          savedViews={!isTodoPublicMode}
        />
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
