import { STATUS_META } from '../constants';
import type { Todo } from '../types';
import { dueLabel, todayStr } from '../util';
import { RecordChip } from './RecordChip';

interface Props {
  todo: Todo;
  onToggleDone: (todo: Todo) => void;
  onToggleStar: (todo: Todo) => void;
  onEdit: (todo: Todo) => void;
  /** Show the status chip (used on Search, where statuses mix). */
  showStatus?: boolean;
}

export function TodoRow({ todo, onToggleDone, onToggleStar, onEdit, showStatus }: Props) {
  const finished = todo.status === 'done' || todo.status === 'dropped';
  const due = todo.due_date ? dueLabel(todo.due_date, todayStr()) : null;
  const hasMeta = todo.context || todo.project_name || todo.deal_title || todo.contact_name
    || todo.tags.length > 0 || due || showStatus;

  return (
    <div className="flex items-start gap-3 rounded-xl border border-line-faint bg-cream px-3 py-2.5 hover:border-brand/40 transition-colors">
      <input
        type="checkbox"
        checked={todo.status === 'done'}
        onChange={() => onToggleDone(todo)}
        className="mt-1 h-5 w-5 shrink-0 accent-green-600 cursor-pointer"
        aria-label={todo.status === 'done' ? 'Mark not done' : 'Mark done'}
      />
      <button type="button" onClick={() => onEdit(todo)} className="min-w-0 flex-1 text-left">
        {/* A todo you can only half-read is a todo you have to open to triage, so the
            title wraps to as many lines as it needs. `break-words` covers the
            pasted-URL case, which has no break opportunity and would otherwise push
            the row's meta chips off screen. */}
        <p className={`text-sm break-words text-charcoal ${finished ? 'line-through text-muted' : ''}`}>
          {todo.title}
          {todo.repeat && (
            <span
              role="img"
              className="ml-1 text-blue-700 dark:text-blue-300"
              title="Recurring"
              aria-label="Recurring"
            >
              ↻
            </span>
          )}
        </p>
        {hasMeta && (
          <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs">
            {showStatus && (
              <span className={`rounded-full px-2 py-0.5 ${STATUS_META[todo.status].chip}`}>
                {STATUS_META[todo.status].label}
              </span>
            )}
            {due && (
              <span className={due.overdue && !finished
                ? 'font-bold text-ck-accent-text'
                : 'text-muted'}>
                {due.text}
              </span>
            )}
            {todo.context && (
              <span className="rounded-full bg-sand px-2 py-0.5 text-muted">{todo.context}</span>
            )}
            {todo.project_name && (
              <span className="rounded-full bg-sand px-2 py-0.5 text-muted">{todo.project_name}</span>
            )}
            <RecordChip todo={todo} />
            {todo.tags.slice(0, 3).map(tag => (
              <span key={tag} className="rounded-full bg-sand px-2 py-0.5 text-muted">#{tag}</span>
            ))}
          </p>
        )}
      </button>
      <button
        type="button"
        onClick={() => onToggleStar(todo)}
        className={`mt-0.5 shrink-0 text-lg leading-none ${
          todo.star ? 'text-ck-amber-text' : 'text-line hover:text-ck-amber-text'
        }`}
        aria-label={todo.star ? 'Unstar' : 'Star as today priority'}
      >
        ★
      </button>
    </div>
  );
}
