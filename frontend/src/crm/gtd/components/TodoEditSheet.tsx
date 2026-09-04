import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { createTodo, deleteTodo, updateTodo } from '../api';
import { REPEAT_OPTIONS, STATUS_META, TODO_STATUS_ORDER } from '../constants';
import { isTodoPublicMode } from '../publicMode';
import type { Todo, TodoProject, TodoStatus } from '../types';
import { parseTags } from '../util';
import { nextActionCopyText, todoCopyText } from '../copyText';
import { CopyButton } from './CopyButton';

const NEW_PROJECT = '__new__';

interface Props {
  /** null = create mode */
  todo: Todo | null;
  /** Defaults for create mode (e.g. the project detail page's inline add). */
  defaults?: Partial<Pick<Todo, 'status' | 'project_id'>>;
  projects: TodoProject[];
  contexts: string[];
  onClose: () => void;
  onSaved: () => void;
}

const inputCls = 'w-full rounded-lg border border-line bg-cream px-3 py-2 text-base sm:text-sm text-charcoal focus:border-brand focus:outline-none';
const labelCls = 'block text-xs font-heading font-semibold text-muted mb-1';

/** Full-field editor — centered modal on desktop, bottom sheet on mobile. */
export function TodoEditSheet({ todo, defaults, projects, contexts, onClose, onSaved }: Props) {
  const [title, setTitle] = useState(todo?.title ?? '');
  const [notes, setNotes] = useState(todo?.notes ?? '');
  const [status, setStatus] = useState<TodoStatus>(todo?.status ?? defaults?.status ?? 'inbox');
  const [projectSel, setProjectSel] = useState<string>(
    String(todo?.project_id ?? defaults?.project_id ?? ''),
  );
  const [newProject, setNewProject] = useState('');
  const [context, setContext] = useState(todo?.context ?? '');
  const [tags, setTags] = useState((todo?.tags ?? []).join(', '));
  const [due, setDue] = useState(todo?.due_date ?? '');
  const [repeat, setRepeat] = useState(todo?.repeat ?? '');
  const [everyN, setEveryN] = useState(
    todo?.repeat?.startsWith('every:') ? todo.repeat.slice(6) : '3',
  );
  const [star, setStar] = useState(todo?.star ?? false);
  const [autoStar, setAutoStar] = useState(todo?.auto_star_on_due ?? false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const isEvery = repeat === 'every' || repeat.startsWith('every:');
  const effectiveRepeat = isEvery ? `every:${everyN || '1'}` : repeat;

  const save = async () => {
    if (!title.trim() || busy) return;
    setBusy(true);
    setError('');
    const fields: Record<string, unknown> = {
      title: title.trim(),
      notes,
      context: context.trim(),
      tags: parseTags(tags),
      status,
      star,
      due_date: due || '',
      repeat: effectiveRepeat,
      // Clearing the repeat rule clears the rule that depends on it, so a todo can
      // never carry a hidden auto-star that reappears if it repeats again.
      auto_star_on_due: effectiveRepeat ? autoStar : false,
    };
    if (projectSel === NEW_PROJECT && newProject.trim()) {
      fields.project = newProject.trim();
    } else {
      fields.project_id = projectSel ? Number(projectSel) : null;
    }
    try {
      if (todo) {
        await updateTodo(todo.id, fields);
      } else {
        await createTodo(fields as Parameters<typeof createTodo>[0]);
      }
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!todo || !window.confirm('Delete this todo permanently? “Dropped” keeps history instead.')) return;
    setBusy(true);
    try {
      await deleteTodo(todo.id);
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed');
      setBusy(false);
    }
  };

  // Copy reads the LIVE form, not `todo` — you can retype the action and copy it
  // before saving, and what lands on the clipboard is what is on screen. Lazy
  // (called on click) so the string is only built when it is wanted.
  const copyFields = () => ({
    title, notes, status,
    projectName: projectSel === NEW_PROJECT
      ? newProject.trim() || null
      : projects.find(p => String(p.id) === projectSel)?.name ?? null,
    context: context.trim(), tags: parseTags(tags),
    dueDate: due || null, repeat: effectiveRepeat, star,
  });

  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-charcoal/40 p-0 sm:p-4"
      onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="max-h-[92dvh] w-full sm:max-w-lg overflow-y-auto rounded-t-2xl sm:rounded-2xl border border-line-faint bg-cream p-5 shadow-lg">
        <div className="flex items-center justify-between gap-3">
          <h2 className="font-heading text-lg font-bold text-charcoal">
            {todo ? 'Edit todo' : 'New todo'}
          </h2>
          {/* Whole todo — the heading names what this button copies. */}
          {title.trim() && (
            <CopyButton text={() => todoCopyText(copyFields())} label="Copy the whole todo" />
          )}
        </div>

        <div className="mt-4 space-y-3">
          <div>
            <div className="mb-1 flex items-center justify-between gap-3">
              <label className={`${labelCls} mb-0`} htmlFor="gtd-title">
                What&rsquo;s the next action?
              </label>
              {/* Just this line — the common case is pasting the action itself
                  into a message, without the status/project scaffolding. */}
              {title.trim() && (
                <CopyButton text={() => nextActionCopyText(title)} label="Copy just the next action" />
              )}
            </div>
            <input id="gtd-title" className={inputCls} value={title} autoFocus
                   onChange={e => setTitle(e.target.value)} />
          </div>
          <div>
            <label className={labelCls} htmlFor="gtd-notes">Notes</label>
            <textarea id="gtd-notes" className={`${inputCls} min-h-20`} value={notes}
                      onChange={e => setNotes(e.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelCls} htmlFor="gtd-status">Status</label>
              <select id="gtd-status" className={inputCls} value={status}
                      onChange={e => setStatus(e.target.value as TodoStatus)}>
                {TODO_STATUS_ORDER.map(s => (
                  <option key={s} value={s}>{STATUS_META[s].label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className={labelCls} htmlFor="gtd-due">Due date</label>
              <input id="gtd-due" type="date" className={inputCls} value={due}
                     onChange={e => setDue(e.target.value)} />
            </div>
            <div>
              <label className={labelCls} htmlFor="gtd-project">Project</label>
              <select id="gtd-project" className={inputCls} value={projectSel}
                      onChange={e => setProjectSel(e.target.value)}>
                <option value="">No project</option>
                {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                <option value={NEW_PROJECT}>+ New project…</option>
              </select>
              {projectSel === NEW_PROJECT && (
                <input className={`${inputCls} mt-2`} placeholder="New project name"
                       aria-label="New project name"
                       value={newProject} onChange={e => setNewProject(e.target.value)} />
              )}
            </div>
            <div>
              <label className={labelCls} htmlFor="gtd-context">Context</label>
              <input id="gtd-context" className={inputCls} list="gtd-contexts"
                     placeholder="@calls" value={context}
                     onChange={e => setContext(e.target.value)} />
              <datalist id="gtd-contexts">
                {contexts.map(c => <option key={c} value={c} />)}
              </datalist>
            </div>
            <div>
              <label className={labelCls} htmlFor="gtd-repeat">Repeat</label>
              <select id="gtd-repeat" className={inputCls}
                      value={isEvery ? 'every' : repeat}
                      onChange={e => setRepeat(e.target.value)}>
                {REPEAT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                <option value="every">Every N days…</option>
              </select>
              {isEvery && (
                <input type="number" min={1} max={9999} value={everyN}
                       aria-label="Repeat every N days"
                       onChange={e => setEveryN(e.target.value)}
                       className={`${inputCls} mt-2`} placeholder="N days" />
              )}
            </div>
            <div>
              <label className={labelCls} htmlFor="gtd-tags">Tags (comma-separated)</label>
              <input id="gtd-tags" className={inputCls} value={tags}
                     onChange={e => setTags(e.target.value)} />
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm text-charcoal">
            <input type="checkbox" checked={star} onChange={e => setStar(e.target.checked)}
                   className="h-4 w-4 accent-amber-500" />
            ★ Today priority
          </label>
          {effectiveRepeat && (
            <div>
              <label className="flex items-center gap-2 text-sm text-charcoal">
                <input type="checkbox" checked={autoStar}
                       onChange={e => setAutoStar(e.target.checked)}
                       className="h-4 w-4 accent-amber-500" />
                ★ Star the next occurrence when it comes due today
              </label>
              <p className="mt-1 pl-6 text-xs text-muted">
                Applies when completing this makes the next one due today, so a routine
                finished late lands straight on the Today list.
              </p>
            </div>
          )}
        </div>

        {todo && (
          <p className="mt-3 text-xs text-muted">
            Source: {todo.source} · Created {new Date(todo.created_at).toLocaleDateString()}
            {todo.completed_at && ` · Completed ${new Date(todo.completed_at).toLocaleDateString()}`}
          </p>
        )}
        {todo && (todo.deal_title || todo.contact_name) && (
          <p className="mt-1 text-xs text-muted">
            Linked to {todo.deal_title ?? todo.contact_name}
            {/* The CRM record pages are behind the login the public surface does not
                have, so the deep link only exists in the authed app. */}
            {!isTodoPublicMode && (
              <>
                {' · '}
                <Link
                  to={todo.deal_id ? `/crm/deals/${todo.deal_id}` : `/crm/contacts/${todo.contact_id}`}
                  className="text-ck-accent-text hover:underline"
                >
                  Open {todo.deal_id ? 'deal' : 'contact'}
                </Link>
              </>
            )}
          </p>
        )}

        {error && <p className="mt-3 text-sm text-ck-accent-text">{error}</p>}

        <div className="mt-5 flex items-center justify-between gap-3">
          {todo ? (
            <button type="button" onClick={() => void remove()} disabled={busy}
                    className="text-sm text-ck-accent-text underline disabled:opacity-50">
              Delete
            </button>
          ) : <span />}
          <div className="flex gap-2">
            <button type="button" onClick={onClose}
                    className="rounded-lg border border-line bg-cream px-4 py-2 text-sm font-heading text-charcoal hover:bg-sand">
              Cancel
            </button>
            <button type="button" onClick={() => void save()} disabled={busy || !title.trim()}
                    className="rounded-lg bg-brand-dark px-4 py-2 text-sm font-heading text-white hover:bg-brand-deep disabled:opacity-40">
              Save
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
