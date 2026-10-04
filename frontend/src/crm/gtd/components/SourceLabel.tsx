import { todoSourceLabel } from '../sourceLabel';
import type { Todo } from '../types';

/**
 * A small "added by" label for a todo a person did not type into the app (#260).
 * Nothing renders for a person's own todo.
 *
 * A plain <span> on purpose: TodoRow's whole body is a <button>, and an interactive
 * element nested there is invalid HTML. The accessible name comes from a visually
 * hidden "Added by " INSIDE the span rather than an aria-label, because a bare span is
 * role=generic and does not reliably take an author-supplied name (the #162 rule) — so
 * the row button reads "… Added by Capture link". Text-only in an existing ink token,
 * deliberately no tint() background (which would owe inkContrast.test.ts a surface).
 */
export function SourceLabel({ source, className = '' }: {
  source: Todo['source'];
  /** Placement classes from an inline host (a margin), applied only when a label renders. */
  className?: string;
}) {
  const label = todoSourceLabel(source);
  if (!label) return null;
  return (
    <span
      className={`rounded-full border border-line-faint px-2 py-0.5 text-muted ${className}`}
      title={label.title}
      data-todo-source={source}
    >
      <span className="sr-only">Added by </span>
      {label.text}
    </span>
  );
}
