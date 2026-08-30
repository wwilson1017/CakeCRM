import { useState } from 'react';
import { todayTodos } from './api';
import { TodoEditSheet } from './components/TodoEditSheet';
import { TodoRow } from './components/TodoRow';
import { useRowActions, useTodos } from './hooks';
import { EmptyState, LoadFailed, LoadingRows, TodoShell } from './TodoShell';
import type { Todo } from './types';
import { useTodoMeta } from './useTodoMeta';
import { todayStr } from './util';

/**
 * The daily home screen: overdue first (they need a decision), then due today, then
 * the starred handful.
 */
export function TodayPage() {
  const { todos, failed, reload } = useTodos(todayTodos);
  const { toggleDone, toggleStar, after } = useRowActions(reload);
  const { projects, filters } = useTodoMeta();
  const [editTodo, setEditTodo] = useState<Todo | null>(null);

  const today = todayStr();
  const overdue = (todos ?? []).filter(t => t.due_date && t.due_date < today);
  const dueToday = (todos ?? []).filter(t => t.due_date === today);
  // A todo already shown as overdue or due-today must not appear twice.
  const seen = new Set([...overdue, ...dueToday].map(t => t.id));
  const starred = (todos ?? []).filter(t => t.star && !seen.has(t.id));

  const section = (title: string, items: Todo[], tone?: string) =>
    items.length > 0 && (
      <section key={title}>
        <h2 className={`mb-2 text-xs font-heading font-bold uppercase tracking-wide ${tone ?? 'text-muted'}`}>
          {title}
        </h2>
        <div className="space-y-2">
          {items.map(t => (
            <TodoRow key={t.id} todo={t} onToggleDone={toggleDone}
                     onToggleStar={toggleStar} onEdit={setEditTodo} />
          ))}
        </div>
      </section>
    );

  return (
    <TodoShell active="today" onAdded={reload}>
      {failed && <LoadFailed retry={reload} />}
      {!failed && todos === null && <LoadingRows />}
      {todos !== null && !failed && (
        overdue.length + dueToday.length + starred.length === 0 ? (
          <EmptyState
            title="Nothing on today's plate"
            hint="Star a task or give it a due date and it shows up here."
          />
        ) : (
          <div className="space-y-6">
            {section('Overdue', overdue, 'text-ck-accent-text')}
            {section('Due today', dueToday)}
            {section('★ Starred', starred, 'text-ck-amber-text')}
          </div>
        )
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
