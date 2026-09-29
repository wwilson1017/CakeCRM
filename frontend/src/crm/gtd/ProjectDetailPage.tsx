import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { createTodo, deleteProject, listProjects, listTodos } from './api';
import { TodoEditSheet } from './components/TodoEditSheet';
import { TodoRow } from './components/TodoRow';
import { PROJECT_STATUSES, PROJECT_STATUS_META } from './constants';
import { useRowActions, useTodosChanged } from './hooks';
import { updateProjectStatus } from './projectActions';
import { todoPath } from './publicMode';
import { LoadFailed, LoadingRows, TodoShell } from './TodoShell';
import type { Todo, TodoProject, TodoProjectStatus } from './types';
import { refreshMeta, useTodoMeta } from './useTodoMeta';

const GROUPS: { title: string; match: (t: Todo) => boolean }[] = [
  { title: 'Next actions', match: t => t.status === 'next_action' },
  { title: 'Waiting / Delegated', match: t => t.status === 'waiting_for' || t.status === 'delegated' },
  { title: 'Someday', match: t => t.status === 'someday_maybe' },
  { title: 'Inbox', match: t => t.status === 'inbox' },
  { title: 'Finished', match: t => t.status === 'done' || t.status === 'dropped' },
];

const inputCls = 'w-full rounded-lg border border-line bg-cream px-3 py-2 text-base sm:text-sm text-charcoal focus:border-brand focus:outline-none';

export function ProjectDetailPage() {
  const { id } = useParams();
  const projectId = Number(id);
  const navigate = useNavigate();
  const [project, setProject] = useState<TodoProject | null>(null);
  const [todos, setTodos] = useState<Todo[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [newAction, setNewAction] = useState('');
  const [editTodo, setEditTodo] = useState<Todo | null>(null);
  const { projects, filters } = useTodoMeta();

  const [loadSeq, setLoadSeq] = useState(0);
  const reload = useCallback(() => { setLoadSeq(s => s + 1); }, []);
  // This page fetches its own project + todos rather than going through `useTodos`, so it
  // subscribes to the undo broadcast itself (#231).
  useTodosChanged(reload);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      listProjects(),
      listTodos({ project: String(projectId), limit: 500 }),
    ]).then(([allProjects, items]) => {
      if (cancelled) return;
      const p = allProjects.find(x => x.id === projectId) ?? null;
      setProject(p);
      setTodos(items);
      setFailed(!p);
    }).catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [projectId, loadSeq]);

  const { toggleDone, toggleStar, after } = useRowActions(reload);

  const addNextAction = async () => {
    if (!newAction.trim()) return;
    await createTodo({ title: newAction.trim(), status: 'next_action', project_id: projectId })
      .catch(() => undefined);
    setNewAction('');
    after();
  };

  const setProjectStatus = async (status: TodoProjectStatus) => {
    if (await updateProjectStatus(projectId, status)) after();
  };

  const removeProject = async () => {
    if (!window.confirm('Delete this project? Its todos survive without a project.')) return;
    await deleteProject(projectId).catch(() => undefined);
    void refreshMeta();
    navigate(todoPath('/projects'));
  };

  const hasNextAction = (todos ?? []).some(t => t.status === 'next_action');

  return (
    <TodoShell active="projects" onAdded={reload}>
      <Link to={todoPath('/projects')} className="text-sm text-muted hover:text-charcoal">
        ← All projects
      </Link>
      {failed && <div className="mt-3"><LoadFailed retry={reload} /></div>}
      {!failed && (project === null || todos === null) && <LoadingRows />}
      {project && todos && (
        <>
          <div className="mt-3 flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="break-words font-heading text-xl font-bold text-charcoal">{project.name}</h2>
              {project.notes && (
                <p className="mt-1 whitespace-pre-wrap text-sm text-muted">{project.notes}</p>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={project.status}
                onChange={e => void setProjectStatus(e.target.value as TodoProjectStatus)}
                className="rounded-lg border border-line bg-cream px-2 py-1.5 text-sm text-charcoal focus:border-brand focus:outline-none"
                aria-label="Project status"
              >
                {PROJECT_STATUSES.map(s => (
                  <option key={s} value={s}>{PROJECT_STATUS_META[s].label}</option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => void removeProject()}
                className="text-sm text-ck-accent-text underline"
              >
                Delete
              </button>
            </div>
          </div>

          {project.status === 'active' && !hasNextAction && (
            <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-700 dark:bg-amber-950/30 dark:text-amber-300">
              This project has no next action. What is the very next physical step?
            </p>
          )}

          <form
            onSubmit={e => { e.preventDefault(); void addNextAction(); }}
            className="mt-4 flex gap-2"
          >
            <input
              className={inputCls}
              placeholder="Add a next action…"
              aria-label="Add a next action"
              value={newAction}
              onChange={e => setNewAction(e.target.value)}
            />
            <button
              type="submit"
              disabled={!newAction.trim()}
              className="shrink-0 rounded-lg bg-brand-dark px-4 py-2 text-sm font-heading text-white hover:bg-brand-deep disabled:opacity-40"
            >
              Add
            </button>
          </form>

          <div className="mt-5 space-y-6">
            {GROUPS.map(g => {
              const items = todos.filter(g.match);
              if (items.length === 0) return null;
              return (
                <section key={g.title}>
                  <h3 className="mb-2 text-xs font-heading font-bold uppercase tracking-wide text-muted">
                    {g.title} ({items.length})
                  </h3>
                  <div className="space-y-2">
                    {items.map(t => (
                      <TodoRow key={t.id} todo={t} onToggleDone={toggleDone}
                               onToggleStar={toggleStar} onEdit={setEditTodo} />
                    ))}
                  </div>
                </section>
              );
            })}
            {todos.length === 0 && (
              <p className="text-sm text-muted">Nothing filed under this project yet.</p>
            )}
          </div>
        </>
      )}
      {editTodo && (
        <TodoEditSheet
          todo={editTodo}
          defaults={{ project_id: projectId }}
          projects={projects}
          contexts={filters?.contexts ?? []}
          onClose={() => setEditTodo(null)}
          onSaved={after}
        />
      )}
    </TodoShell>
  );
}
