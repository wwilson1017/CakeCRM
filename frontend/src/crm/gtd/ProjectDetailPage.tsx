import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ApiError } from '../../core/api/client';
import { createTodo, deleteProject, listProjects, listTodos, updateProject } from './api';
import { InlineTitle } from './components/InlineTitle';
import { TodoEditSheet } from './components/TodoEditSheet';
import { TodoRow } from './components/TodoRow';
import { PROJECT_STATUSES, PROJECT_STATUS_META } from './constants';
import { useRowActions, useTodosChanged } from './hooks';
import { updateProjectStatus } from './projectActions';
import { isTypingTarget, projectNeighbours } from './projectNav';
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

/** The project fields edited in place on this page — each saved on its own (#232, #262). */
type EditableField = 'name' | 'notes' | 'purpose' | 'outcome';

const PROJECT_LINES: { field: 'purpose' | 'outcome'; label: string; placeholder: string }[] = [
  { field: 'purpose', label: 'Purpose', placeholder: 'Why does this project exist?' },
  { field: 'outcome', label: 'Outcome', placeholder: 'What does done look like?' },
];

const inputCls = 'w-full rounded-lg border border-line bg-cream px-3 py-2 text-base sm:text-sm text-charcoal focus:border-brand focus:outline-none';

/**
 * Keyed by the route id so the ‹ › arrows (#264) land on a FRESH page. Moving between
 * projects keeps this route matched, so without the key every piece of page state — the
 * loaded project, a field's failure line, an open edit sheet, a half-typed next action —
 * would carry over and describe the previous project until the new fetch landed.
 */
export function ProjectDetailPage() {
  const { id } = useParams();
  return <ProjectDetail key={id} projectId={Number(id)} />;
}

function ProjectDetail({ projectId }: { projectId: number }) {
  const navigate = useNavigate();
  const [project, setProject] = useState<TodoProject | null>(null);
  const [todos, setTodos] = useState<Todo[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [newAction, setNewAction] = useState('');
  const [editTodo, setEditTodo] = useState<Todo | null>(null);
  /**
   * ONE failure line for both click-to-edit fields, TAGGED with the field it belongs to.
   *
   * The tag is not bookkeeping — without it the message can be wiped by the other field.
   * Both editors can be open at once (clicking the notes trigger is what blurs the name
   * editor, and a REFUSED name save leaves that editor open), so closing the notes editor
   * unchanged would clear the very refusal the name save had just reported. Each field only
   * ever clears its own.
   */
  const [saveError, setSaveError] = useState<{ field: EditableField; text: string } | null>(null);
  const clearErrorFor = (field: EditableField) =>
    setSaveError(e => (e?.field === field ? null : e));
  const { projects, filters } = useTodoMeta();

  // Previous / next among projects of the same status, in the Projects tab's order (#264).
  const neighbours = projectNeighbours(projects, projectId);
  const prevId = neighbours?.prev.id;
  const nextId = neighbours?.next.id;
  const sheetOpen = editTodo !== null;
  useEffect(() => {
    if (prevId === undefined || nextId === undefined || sheetOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      // Never while typing — the inline name / notes editors, the add box, Quick Add — and
      // never on a chord, which belongs to the browser or the OS.
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey
        || isTypingTarget(e.target)) return;
      navigate(todoPath(`/projects/${e.key === 'ArrowLeft' ? prevId : nextId}`));
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [prevId, nextId, sheetOpen, navigate]);

  const [loadSeq, setLoadSeq] = useState(0);
  const reload = useCallback(() => { setLoadSeq(s => s + 1); }, []);
  // This page fetches its own project + todos rather than going through `useTodos`, so it
  // subscribes to the undo broadcast itself (#231).
  useTodosChanged(reload);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      listProjects(),
      // A project's own page shows its deferred todos too (#261), each saying when it
      // comes back — this is a record view, not a working list.
      listTodos({ project: String(projectId), limit: 500, include_deferred: true }),
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

  /**
   * Persist ONE project field — only the field that changed, never `status` riding along.
   *
   * It merges back only the field it WROTE rather than adopting the whole response row: every
   * editor writes through here and none blocks another, so several PUTs can be in flight at
   * once, and adopting a slower response wholesale would carry a SIBLING field's pre-write
   * value and revert an edit the user already watched save. `refreshMeta()` is what carries a
   * rename off this page — the meta cache feeds the Projects list, the todo rows and
   * `TodoEditSheet`'s project picker. Resolving false is the contract with `InlineTitle`: the
   * editor stays open holding the typed text under the failure line this sets.
   */
  const saveField = async (field: EditableField, value: string): Promise<boolean> => {
    clearErrorFor(field);
    try {
      const saved = await updateProject(projectId, { [field]: value });
      // Merge back the SERVER's value for this one field: names, purpose and outcome are
      // trimmed on the way in, so the typed text is not necessarily what was stored.
      setProject(p => (p ? { ...p, [field]: saved[field], updated_at: saved.updated_at } : saved));
      void refreshMeta();
      return true;
    } catch (e) {
      // The server's own `detail` is the sentence worth showing — the duplicate-name refusal
      // is `Project "X" already exists`, which names the conflict. `ApiError.message` wraps
      // it in the status code, so read the field, not the message.
      const text = e instanceof ApiError && e.detail ? e.detail
        : e instanceof Error ? e.message : 'Could not save. Your change was not stored.';
      setSaveError({ field, text });
      return false;
    }
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
      <div className="flex items-center justify-between gap-3">
        <Link to={todoPath('/projects')} className="text-sm text-muted hover:text-charcoal">
          ← All projects
        </Link>
        {neighbours && (
          <div className="flex gap-1">
            {([['Previous project', '‹', neighbours.prev], ['Next project', '›', neighbours.next]] as const)
              .map(([label, arrow, to]) => (
                <Link
                  key={label}
                  to={todoPath(`/projects/${to.id}`)}
                  aria-label={`${label}: ${to.name}`}
                  title={`${label}: ${to.name}`}
                  className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-line bg-cream text-muted hover:bg-sand hover:text-charcoal focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
                >
                  <span aria-hidden="true">{arrow}</span>
                </Link>
              ))}
          </div>
        )}
      </div>
      {failed && <div className="mt-3"><LoadFailed retry={reload} /></div>}
      {!failed && (project === null || todos === null) && <LoadingRows />}
      {project && todos && (
        <>
          <div className="mt-3 flex flex-wrap items-start justify-between gap-3">
            {/* `grow` is load-bearing: sized to its text's max-content, this column did not
                include the padding and border the click-to-edit trigger adds, so the trigger's
                `max-w-full` capped it short of its own single-line width and every multi-word
                name broke at the last space. Basis stays auto, so it still wraps on a phone. */}
            <div className="min-w-0 grow">
              {/* The heading stays an `<h2>` and the control renders INSIDE it, so the page
                  keeps its heading semantics while the words become the control. `-ml-1`
                  cancels the control's own padding so the text lines up with the notes. */}
              <h2 className="font-heading text-xl font-bold text-charcoal">
                <InlineTitle
                  title={project.name}
                  label="Project name"
                  onSave={name => saveField('name', name)}
                  // A blank name is refused by the control before it reaches the server, so this
                  // is the only place that refusal can be explained. Every other close (Escape,
                  // or nothing changed) just clears whatever line was up.
                  onCancel={reason => (reason === 'blank'
                    ? setSaveError({
                      field: 'name',
                      text: 'A project name is required — the previous name was kept.',
                    })
                    : clearErrorFor('name'))}
                  className="-ml-1 font-heading text-xl font-bold text-charcoal"
                />
              </h2>
              {/* Always rendered, even with no notes: the placeholder IS the affordance. Before
                  this the element was absent when blank, so notes could never be ADDED. */}
              <InlineTitle
                title={project.notes}
                label="Project notes"
                variant="body"
                placeholder="Add notes…"
                onSave={notes => saveField('notes', notes)}
                onCancel={() => clearErrorFor('notes')}
                className="-ml-1 mt-1 whitespace-pre-wrap text-sm text-muted"
              />
              {/* Purpose and outcome (#262): one line each, always rendered so an empty one is
                  still something to click. The `line` variant: Enter saves as for a name, and
                  clearing is a real save, where a `title` refuses a blank as a slip. Purpose first:
                  why before what done looks like, the order the triage card shows them in. */}
              <dl className="mt-2 space-y-1 text-sm">
                {PROJECT_LINES.map(({ field, label, placeholder }) => (
                  <div key={field} className="flex items-baseline gap-2">
                    <dt className="shrink-0 font-heading text-xs font-bold uppercase tracking-wide text-muted">
                      {label}
                    </dt>
                    <dd className="min-w-0 grow">
                      <InlineTitle
                        title={project[field]}
                        label={`Project ${field}`}
                        variant="line"
                        placeholder={placeholder}
                        onSave={value => saveField(field, value)}
                        onCancel={() => clearErrorFor(field)}
                        className="-ml-1 text-sm text-charcoal"
                      />
                    </dd>
                  </div>
                ))}
              </dl>
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

          {/* Under the header rather than inside it: a duplicate-name refusal names the
              conflicting project and needs the full width. `role="alert"` because the eye is on
              the editor, which stays open above this with the typed text intact. */}
          {saveError && (
            <p role="alert" className="mt-2 text-sm text-ck-accent-text">{saveError.text}</p>
          )}

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
