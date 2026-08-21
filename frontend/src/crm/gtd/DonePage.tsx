import { useState } from 'react';
import { listTodos } from './api';
import { TodoEditSheet } from './components/TodoEditSheet';
import { TodoRow } from './components/TodoRow';
import { useRowActions, useTodos } from './hooks';
import { EmptyState, LoadFailed, LoadingRows, TodoShell } from './TodoShell';
import type { Todo, TodoStatus } from './types';
import { useTodoMeta } from './useTodoMeta';

/**
 * Completed and dropped work, newest first.
 *
 * The status toggle is the fetch key, not a client-side filter: these lists grow
 * forever, so the server orders them newest-finished-first under a LIMIT and a
 * client-side split would show whatever happened to be in the window.
 */
export function DonePage() {
  const [status, setStatus] = useState<TodoStatus>('done');
  const { todos, failed, reload } = useTodos(
    () => listTodos({ status, limit: 200 }), status,
  );
  const { toggleDone, toggleStar, after } = useRowActions(reload);
  const { projects, filters } = useTodoMeta();
  const [editTodo, setEditTodo] = useState<Todo | null>(null);

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
      {todos !== null && !failed && todos.length === 0 && (
        <EmptyState
          title={status === 'done' ? 'Nothing completed yet' : 'Nothing dropped'}
          hint={status === 'done'
            ? 'Finished work lands here, newest first.'
            : 'Dropping keeps an item out of your lists without deleting it.'}
        />
      )}
      <div className="space-y-2">
        {(todos ?? []).map(t => (
          <TodoRow key={t.id} todo={t} onToggleDone={toggleDone}
                   onToggleStar={toggleStar} onEdit={setEditTodo} />
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
