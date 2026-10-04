import { useEffect, useState } from 'react';
import { CollectionView, useCollectionState } from '../../shared/collection';
import { createProject, listProjects, listTodos } from './api';
import { projectsCollectionConfig } from './collectionConfig';
import { ProjectCard } from './components/ProjectCard';
import { PROJECT_STATUSES, PROJECT_STATUS_META } from './constants';
import { updateProjectStatus } from './projectActions';
import { isTodoPublicMode } from './publicMode';
import { useTodosChanged } from './hooks';
import { EmptyState, LoadFailed, LoadingRows, TodoShell } from './TodoShell';
import type { TodoProject, TodoProjectStatus } from './types';
import { refreshMeta } from './useTodoMeta';

const inputCls = 'w-full rounded-lg border border-line bg-cream px-3 py-2 text-base sm:text-sm text-charcoal focus:border-brand focus:outline-none';
const PAGE_LIMIT = 500;
const NO_PROJECTS: TodoProject[] = [];

/**
 * Projects = outcomes needing more than one action. The GTD rule this page enforces:
 * every ACTIVE project should have at least one next action, and the card warns when
 * it doesn't.
 *
 * The grid runs through the shared collection layer's `cards` view (#234), which adds
 * search over name and notes. The status tabs stay a page-owned FETCH key rather than a
 * facet, and the cell is the page's own `ProjectCard` (see its docstring for why).
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
    Promise.all([listProjects(s), listTodos({ status: 'next_action', limit: PAGE_LIMIT, include_deferred: true })])
      .then(([p, next]) => {
        setProjects(p);
        // A full window can't prove absence — mark every project as having a next
        // action so no card is falsely flagged stalled. Deferred next actions count (#261): a
    // project whose next step is scheduled to come back is not stalled.
        setProjectsWithNext(next.length >= PAGE_LIMIT
          ? new Set(p.map(x => x.id))
          : new Set(next.map(t => t.project_id).filter((id): id is number => id !== null)));
        setFailed(false);
      })
      .catch(() => setFailed(true));
  };
  useEffect(() => { load(status); }, [status]);
  // The stalled-project warning reads next actions, so an undo that reopens (or
  // re-completes) one has to refetch here too (#231) — this page owns its own fetch.
  useTodosChanged(() => load(status));

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

  const closeOut = async (p: TodoProject) => {
    const reopening = p.status === 'completed' || p.status === 'dropped';
    if (await updateProjectStatus(p.id, reopening ? 'active' : 'completed')) {
      load(status);
      void refreshMeta();
    }
  };

  const items = projects ?? NO_PROJECTS;
  // A module-constant config needs no memoization to meet the layer's stability contract.
  const state = useCollectionState(projectsCollectionConfig, items);

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
      {!failed && projects !== null && projects.length === 0 && (
        <EmptyState
          title={`No ${PROJECT_STATUS_META[status].label.toLowerCase()} projects`}
          hint="A project is any outcome that needs more than one action."
        />
      )}
      {!failed && items.length > 0 && (
        <CollectionView
          config={projectsCollectionConfig}
          state={state}
          items={items}
          searchPlaceholder="Search projects…"
          savedViews={!isTodoPublicMode}
          cards={{
            renderCard: p => (
              <ProjectCard
                project={p}
                stalled={p.status === 'active' && !projectsWithNext.has(p.id)}
                onCloseOut={project => void closeOut(project)}
              />
            ),
          }}
        />
      )}
    </TodoShell>
  );
}
