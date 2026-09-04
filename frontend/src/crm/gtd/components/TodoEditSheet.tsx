import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { createTodo, deleteTodo, updateTodo } from '../api';
import { REPEAT_OPTIONS, STATUS_META, TODO_STATUS_ORDER } from '../constants';
import { isTodoPublicMode } from '../publicMode';
import type { Todo, TodoProject, TodoStatus } from '../types';
import { parseTags } from '../util';

const NEW_PROJECT = '__new__';
// The context picker's option values are INDICES into `contextOptions`, never the context
// strings — the same reason TriageCard.tsx gives: a context a user really named `__new__`
// would otherwise collide with the sentinel and be impossible to select.
const NEW_CONTEXT = '__new__';

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
  // true = the picker has been swapped for the "type a brand new one" input.
  const [addingContext, setAddingContext] = useState(false);
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

  // The SELECTED context always gets an option, whether or not the shared meta lists it.
  // Two ways it can be missing: the meta loads async (so a todo's own context isn't there
  // on the first paint), and a refresh while this sheet is open can DROP a context the user
  // just picked. Either way an option list without the current selection renders a select
  // matching no option, while `save()` still submits the hidden string — the control and
  // the payload disagreeing is worse than an extra option.
  //
  // While the create input is open the todo's original stands in, so the list does not
  // reshuffle on every keystroke of a name being typed (the select is showing the sentinel
  // then, so its contents do not matter).
  const contextOptions = useMemo(() => {
    const known = contexts.filter(Boolean);
    const own = (addingContext ? (todo?.context ?? '') : context).trim();
    return own && !known.includes(own) ? [own, ...known] : known;
  }, [contexts, context, addingContext, todo?.context]);

  // Derived every render from the context STRING rather than held in state: the meta list
  // arrives after the first render, and an index frozen at mount would then point at a
  // different context.
  //
  // Trimmed on BOTH sides. `contextOptions` inserts the trimmed value, and `save()` submits
  // the trimmed value, so looking the raw one up would miss for a stored context carrying
  // stray whitespace: `indexOf` returns -1, no option has value "-1", and the select falls
  // back to reading "No context" for a todo that plainly has one.
  const contextSel = addingContext
    ? NEW_CONTEXT
    : (context.trim() ? String(contextOptions.indexOf(context.trim())) : '');

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

  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-charcoal/40 p-0 sm:p-4"
      onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="max-h-[92dvh] w-full sm:max-w-lg overflow-y-auto rounded-t-2xl sm:rounded-2xl border border-line-faint bg-cream p-5 shadow-lg">
        <h2 className="font-heading text-lg font-bold text-charcoal">
          {todo ? 'Edit todo' : 'New todo'}
        </h2>

        <div className="mt-4 space-y-3">
          <div>
            <label className={labelCls} htmlFor="gtd-title">What&rsquo;s the next action?</label>
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
              {/* A <select>, not an <input list>/<datalist>: mobile browsers do not
                  reliably render a datalist on a POPULATED text input, so the picker was
                  invisible until the field was cleared. This is the same
                  select-plus-escape-hatch shape the Inbox triage card already uses, and the
                  shape Status/Project/Repeat use in this very form. */}
              <select id="gtd-context" className={inputCls} value={contextSel}
                      onChange={e => {
                        const v = e.target.value;
                        if (v === NEW_CONTEXT) { setAddingContext(true); setContext(''); return; }
                        setAddingContext(false);
                        setContext(v ? contextOptions[Number(v)] ?? '' : '');
                      }}>
                <option value="">No context</option>
                {contextOptions.map((c, i) => <option key={c} value={i}>{c}</option>)}
                <option value={NEW_CONTEXT}>+ New context…</option>
              </select>
              {addingContext && (
                <input className={`${inputCls} mt-2`} placeholder="@calls"
                       aria-label="New context" value={context}
                       onChange={e => setContext(e.target.value)} />
              )}
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
