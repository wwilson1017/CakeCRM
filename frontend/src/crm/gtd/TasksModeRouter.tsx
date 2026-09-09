import { lazy } from 'react';
import { Navigate } from 'react-router-dom';
import { useTaskMode } from './TaskModeContext';

// Lazy at MODULE scope (#149): TasksPage drags the whole collection layer and @dnd-kit with
// it, and this module is imported eagerly by App.tsx — a static import here would put all of
// that into the shell chunk every CRM visitor downloads, the login page included.
const TasksPage = lazy(() => import('../TasksPage').then((m) => ({ default: m.TasksPage })));

interface Props {
  /** The GTD page for this route. */
  gtd: React.ReactNode;
  /**
   * What normal mode renders. The bare /crm/tasks route renders the existing flat
   * Tasks page; the GTD-only sub-routes (inbox, projects, …) have no normal-mode
   * equivalent and redirect back to /crm/tasks instead.
   */
  normal?: 'tasks' | 'redirect';
}

/**
 * `/crm/tasks` renders by mode: normal shows today's Tasks page untouched, GTD shows
 * the ported GTD shell. One task system is visible at a time — with a single store
 * behind both, a second surface would only show what the first already does.
 *
 * Renders nothing until the mode is known, rather than defaulting to normal: guessing
 * would flash the wrong task system on every load of a GTD-mode install. Both page
 * elements are lazy components; neither suspends until it is actually rendered, and the
 * Suspense boundary that catches it is `CrmLayout`'s, around the content column — so the
 * nav stays put while a task chunk loads.
 */
export function TasksModeRouter({ gtd, normal = 'tasks' }: Props) {
  const mode = useTaskMode();
  if (mode === null) return null;
  if (mode === 'gtd') return <>{gtd}</>;
  return normal === 'tasks' ? <TasksPage /> : <Navigate to="/crm/tasks" replace />;
}
