import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { createProject, listProjects, listTodos } from './api';
import { PROJECT_STATUSES, PROJECT_STATUS_META } from './constants';
import { updateProjectStatus } from './projectActions';
import { todoPath } from './publicMode';
import { EmptyState, LoadFailed, LoadingRows, TodoShell } from './TodoShell';
import type { TodoProject, TodoProjectStatus } from './types';
import { refreshMeta } from './useTodoMeta';

const inputCls = 'w-full rounded-lg border border-line bg-cream px-3 py-2 text-base sm:text-sm text-charcoal focus:border-brand focus:outline-none';
const PAGE_LIMIT = 500;

/**
 * Projects = outcomes needing more than one action. The GTD rule this page enforces:
 * every ACTIVE project should have at least one next action, and the card warns when
 * it doesn't.
 *
 * Rendered as a plain card grid rather than through `shared/collection`. #73 landed
 * that layer without rewiring any CRM surface — adopting it across the list pages is
 * its own issue (#77) — so this page follows the current house state and joins that
 * migration with the rest.
 */
export function ProjectsPage() {
  const [status, setStatus] = useState<TodoProjectStatus>('active');
  const [projects, setProjects] = useState<TodoProject[] | null>(null);
  const [projectsWithNext, setProjectsWithNext] = useState<Set<number>>(new Set());
  const [failed, setFailed] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState('');
  const [notes, setNotes] = useState('');
  const [formError, setFormError] = useState('');

  const load = (s: TodoProjectStatus) => {
    // Next actions ride along so the stalled warning measures the GTD thing ("no next
    // action"), not open_count — someday/waiting items keep open_count > 0 on a
    // project that is still stalled.
    Promise.all([listProjects(s), listTodos({ status: 'next_action', limit: PAGE_LIMIT })])
      .then(([p, next]) => {
        setProjects(p);
        // A full window can't prove absence — mark every project as having a next
        // action so no card is falsely flagged stalled.
        setProjectsWithNext(next.length >= PAGE_LIMIT
          ? new Set(p.map(x => x.id))
          : new Set(next.map(t => t.project_id).filter((id): id is number => id !== null)));
        setFailed(false);
      })
      .catch(() => setFailed(true));
  };
  useEffect(() => { load(status); }, [status]);

  const add = async () => {
    if (!name.trim()) return;
    setFormError('');
    try {
      await createProject({ name: name.trim(), notes });
      setName(''); setNotes(''); setShowForm(false);
      load(status);
      void refreshMeta();
    } catch (e) {
      setFormError(e instanceof Error ? e.message : 'Create failed');
    }
  };

  const closeOut = async (e: React.MouseEvent, p: TodoProject) => {
    // The card is a Link; the button must not navigate.
    e.preventDefault();
    e.stopPropagation();
    const reopening = p.status === 'completed' || p.status === 'dropped';
    if (await updateProjectStatus(p.id, reopening ? 'active' : 'completed')) {
      load(status);
      void refreshMeta();
    }
  };

  return (
    <TodoShell active="projects" onAdded={() => load(status)}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-1.5">
          {PROJECT_STATUSES.map(s => (
            <button
              key={s}
              type="button"
              onClick={() => setStatus(s)}
              aria-pressed={status === s}
              className={`rounded-lg px-3 py-1.5 text-sm font-heading transition-colors ${
                status === s
                  ? 'bg-brand-dark text-white'
                  : 'border border-line bg-cream text-muted hover:bg-sand'
              }`}
            >
              {PROJECT_STATUS_META[s].label}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => setShowForm(v => !v)}
          className="rounded-lg bg-brand-dark px-3 py-1.5 text-sm font-heading text-white hover:bg-brand-deep"
        >
          {showForm ? 'Cancel' : 'New project'}
        </button>
      </div>

      {showForm && (
        <form
          onSubmit={e => { e.preventDefault(); void add(); }}
          className="mb-4 space-y-2 rounded-xl border border-line bg-cream p-4"
        >
          <input
            className={inputCls}
            placeholder="Project name — the outcome you want"
            aria-label="Project name"
            autoFocus
            value={name}
            onChange={e => setName(e.target.value)}
          />
          <textarea
            className={`${inputCls} min-h-16`}
            placeholder="Notes (optional)"
            aria-label="Project notes"
            value={notes}
            onChange={e => setNotes(e.target.value)}
          />
          {formError && <p className="text-sm text-ck-accent-text">{formError}</p>}
          <button
            type="submit"
            disabled={!name.trim()}
            className="rounded-lg bg-brand-dark px-4 py-2 text-sm font-heading text-white hover:bg-brand-deep disabled:opacity-40"
          >
            Create project
          </button>
        </form>
      )}

      {failed && <LoadFailed retry={() => load(status)} />}
      {!failed && projects === null && <LoadingRows />}
      {projects !== null && !failed && projects.length === 0 && (
        <EmptyState
          title={`No ${PROJECT_STATUS_META[status].label.toLowerCase()} projects`}
          hint="A project is any outcome that needs more than one action."
        />
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        {(projects ?? []).map(p => {
          const stalled = p.status === 'active' && !projectsWithNext.has(p.id);
          return (
            <Link
              key={p.id}
              to={todoPath(`/projects/${p.id}`)}
              className="block rounded-xl border border-line-faint bg-cream p-4 transition hover:border-brand/50"
            >
              <div className="flex items-start justify-between gap-2">
                <p className="min-w-0 break-words font-heading font-semibold text-charcoal">{p.name}</p>
                <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${PROJECT_STATUS_META[p.status].chip}`}>
                  {PROJECT_STATUS_META[p.status].label}
                </span>
              </div>
              {p.notes && <p className="mt-1 line-clamp-2 text-sm text-muted">{p.notes}</p>}
              <div className="mt-3 flex items-center justify-between gap-2">
                <span className="text-xs text-muted">
                  {p.open_count} open item{p.open_count === 1 ? '' : 's'}
                </span>
                <button
                  type="button"
                  onClick={e => void closeOut(e, p)}
                  className="rounded-lg border border-line px-2.5 py-1 text-xs font-heading text-charcoal hover:bg-sand"
                >
                  {p.status === 'completed' || p.status === 'dropped' ? 'Reactivate' : 'Complete'}
                </button>
              </div>
              {stalled && (
                <p className="mt-2 rounded-lg bg-amber-50 px-2 py-1 text-xs text-amber-700 dark:bg-amber-950/30 dark:text-amber-300">
                  No next action — decide the very next physical step.
                </p>
              )}
            </Link>
          );
        })}
      </div>
    </TodoShell>
  );
}
