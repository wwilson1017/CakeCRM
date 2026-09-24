import { lazy } from 'react';
import { Navigate } from 'react-router-dom';
import { useTodoMode } from './TodoModeContext';

// Lazy at MODULE scope (#149): TodosPage drags the whole collection layer and @dnd-kit with
// it, and this module is imported eagerly by App.tsx — a static import here would put all of
// that into the shell chunk every CRM visitor downloads, the login page included.
const TodosPage = lazy(() => import('../TodosPage').then((m) => ({ default: m.TodosPage })));

interface Props {
  /** The GTD page for this route. */
  gtd: React.ReactNode;
  /**
   * What normal mode renders. The bare /crm/todos route renders the existing flat
   * Todos page; the GTD-only sub-routes (inbox, projects, …) have no normal-mode
   * equivalent and redirect back to /crm/todos instead.
   */
  normal?: 'todos' | 'redirect';
}

/**
 * `/crm/todos` renders by mode: normal shows today's Todos page untouched, GTD shows
 * the ported GTD shell. One todo system is visible at a time — with a single store
 * behind both, a second surface would only show what the first already does.
 *
 * Renders nothing until the mode is known, rather than defaulting to normal: guessing
 * would flash the wrong todo system on every load of a GTD-mode install. Both page
 * elements are lazy components; neither suspends until it is actually rendered, and the
 * Suspense boundary that catches it is `CrmLayout`'s, around the content column — so the
 * nav stays put while a todo chunk loads.
 */
export function TodosModeRouter({ gtd, normal = 'todos' }: Props) {
  const mode = useTodoMode();
  if (mode === null) return null;
  if (mode === 'gtd') return <>{gtd}</>;
  return normal === 'todos' ? <TodosPage /> : <Navigate to="/crm/todos" replace />;
}
