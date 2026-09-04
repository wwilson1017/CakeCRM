import { useMemo, useState } from 'react';
import { toast } from '../../../shared/toast';
import { createProject, deleteTodo, updateTodo } from '../api';
import type { Todo, TodoProject, TodoStatus } from '../types';
import { InlineTitle } from './InlineTitle';
import { RecordChip } from './RecordChip';

interface Props {
  todo: Todo;
  projects: TodoProject[];
  /** Contexts already in use — the options for the final "set context" step. */
  contexts: string[];
  /** After a write that takes the item OUT of the inbox (filed/done/deleted). */
  onProcessed: () => void;
  /** After an in-place change that leaves it in the inbox (title, project, …). */
  onChanged: () => void;
  onEdit: (todo: Todo) => void;
}

// Where the item lands once it is filed. Picking one is a LOCAL decision — nothing is
// written until the context step, so the item stays in the inbox (and on this card)
// while you keep clarifying it.
const DESTINATIONS: { label: string; status: TodoStatus; list: string }[] = [
  { label: 'Next action', status: 'next_action', list: 'To Do' },
  { label: 'Waiting', status: 'waiting_for', list: 'Waiting For' },
  { label: 'Delegated', status: 'delegated', list: 'Delegated' },
  { label: 'Someday', status: 'someday_maybe', list: 'Someday / Maybe' },
];

// The context picker's option values are INDICES, never the context strings: a context
// a user really named "new" would otherwise collide with the sentinel and be
// impossible to select.
const NEW_CONTEXT = 'new';
// The project picker's values are numeric ids, so a non-numeric sentinel cannot
// collide with a real one however the project is named.
const NEW_PROJECT = 'new';

const stepCls = 'mb-2 text-xs font-heading font-bold uppercase tracking-wide text-muted';
const destCls = 'rounded-lg border px-3 py-2 text-sm font-heading transition-colors disabled:opacity-50';
const inputCls = 'rounded-lg border border-line bg-cream px-2 py-1.5 text-sm text-charcoal focus:border-brand focus:outline-none disabled:opacity-50';
const linkCls = 'text-sm underline disabled:opacity-50';

/**
 * GTD triage — one inbox item at a time, as three deliberate clarify steps: what kind
 * of action it is, optional detail, then the context that files it. Setting a context
 * is the ONLY way out of the inbox.
 */
export function TriageCard({ todo, projects, contexts, onProcessed, onChanged, onEdit }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [destination, setDestination] = useState<TodoStatus>('next_action');
  // A rename this card just made, until the parent's reload feeds it back in through
  // `todo`. Blur-then-click means clicking Edit STARTS the rename and opens the sheet
  // in the same gesture, and the sheet snapshots the title it is handed — so passing
  // the stale prop would silently revert the rename the user just made.
  const [pendingTitle, setPendingTitle] = useState<string | null>(null);
  // null = the picker is showing; a string = the inline create input is.
  const [newContext, setNewContext] = useState<string | null>(null);
  const [newProject, setNewProject] = useState<string | null>(null);
  const dest = DESTINATIONS.find(d => d.status === destination) ?? DESTINATIONS[0];

  // An inbox item can already carry a context (quick-add parses "@ctx" while keeping
  // status: inbox), and the shared meta can still be empty while it loads — offer the
  // item's own context either way, so the card always has a way out that isn't
  // "retype what is already there".
  const options = useMemo(() => {
    const known = contexts.filter(Boolean);
    return todo.context && !known.includes(todo.context) ? [todo.context, ...known] : known;
  }, [contexts, todo.context]);

  /** One write. `resolves` says whether it takes the item out of the inbox. */
  const patch = async (fields: Record<string, unknown>, resolves: boolean): Promise<boolean> => {
    if (busy) return false;
    setBusy(true);
    setError('');
    try {
      await updateTodo(todo.id, fields);
      if (resolves) {
        // Deliberately STAYS busy — this card is spent. The parent's reload is async
        // and keeps rendering the just-filed todo until it lands; re-enabling in that
        // window would let a second write hit an item that has already left the inbox
        // (picking a context right after "Done ✓" would reopen a completed — and by
        // then already re-spawned — repeating todo). If the reload fails the card
        // stays disabled, which is the intended trade-off: the page renders its own
        // retry banner, so there is a way back.
        onProcessed();
      } else {
        setBusy(false);
        onChanged();
      }
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Update failed');
      setBusy(false);
      return false;
    }
  };

  /** The only exit from the inbox: the context and the chosen destination in ONE
   * write. A context is always required — there is deliberately no "file anyway". */
  const fileUnder = async (context: string) => {
    const value = context.trim();
    if (busy || !value) return;
    if (await patch({ context: value, status: destination }, true)) {
      setNewContext(null);
      toast.info(`Filed under ${value} → ${dest.list}`);
    }
  };

  /**
   * Create a project mid-triage and file this item under it. Name only — the outcome
   * and notes belong to the weekly review, not to clarifying one inbox item.
   *
   * Deliberately not `patch()`: this is TWO writes, and the interesting state is the
   * one between them. Once the project exists, a failed assignment is not "creating
   * the project failed" — reporting it that way sends the user back to create a
   * duplicate, which the unique-name constraint then rejects.
   */
  const createAndAssign = async (name: string) => {
    const value = name.trim();
    if (busy || !value) return;
    setBusy(true);
    setError('');
    let created: TodoProject;
    try {
      created = await createProject({ name: value });
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Could not create the project';
      // Inline, so a rejected name — a duplicate, most often — is correctable right
      // there in the still-open input. AND a toast, because a rejected POST does not
      // prove nothing was created (the row can commit and the response be lost), so
      // refresh the pickable list rather than leave a project that exists missing
      // from the dropdown the retry is about to collide with.
      setError(msg);
      toast.error(msg);
      setBusy(false);
      onChanged();
      return;
    }
    // The project EXISTS from here on, whatever happens next.
    setNewProject(null);
    try {
      await updateTodo(todo.id, { project_id: created.id });
    } catch {
      // A TOAST, not this card's inline error: promoting another queue row is not
      // gated on `busy`, so the user can swap the head item while these two writes
      // are in flight — the page keys the card by todo id, so this one unmounts and
      // an inline setError would land on a dead component and vanish. That is exactly
      // the case that must not go quiet: the project now exists and nothing on screen
      // would say so.
      toast.error(`Created “${created.name}”, but couldn't file this item under it — pick it from the list.`);
    }
    setBusy(false);
    onChanged(); // reloads the todo AND the project list, so the new one is pickable
  };

  // Title saves keep their own in-flight state rather than taking the card-wide
  // `busy`: clicking a button is what blurs the editor, and a shared flag would
  // swallow that very click. The two writes touch disjoint fields and the service
  // locks the row, so overlapping them is safe.
  const saveTitle = async (title: string): Promise<boolean> => {
    setError('');
    setPendingTitle(title);
    try {
      await updateTodo(todo.id, { title });
      onChanged();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Update failed');
      setPendingTitle(null); // never written — don't let the sheet adopt it
      return false;
    }
  };

  // What the user believes this todo is called right now. Once the reload lands,
  // `todo.title` catches up and the merge is a no-op.
  const current = pendingTitle === null ? todo : { ...todo, title: pendingTitle };

  const remove = async () => {
    if (busy) return;
    if (!window.confirm('Delete this todo permanently?')) return;
    setBusy(true);
    setError('');
    try {
      await deleteTodo(todo.id);
      onProcessed(); // stays busy, same reason as patch()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed');
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-line bg-cream p-4 sm:p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <InlineTitle
          title={current.title}
          label="Todo title"
          disabled={busy}
          onSave={saveTitle}
          className="font-heading text-lg font-semibold text-charcoal"
        />
        <button
          type="button"
          disabled={busy}
          onClick={() => void patch({ star: !todo.star }, false)}
          className={`shrink-0 text-xl leading-none disabled:opacity-50 ${
            todo.star ? 'text-ck-amber-text' : 'text-line hover:text-ck-amber-text'
          }`}
          aria-label="Star as today priority"
        >
          ★
        </button>
      </div>
      {todo.notes && <p className="mt-1 whitespace-pre-wrap text-sm text-muted">{todo.notes}</p>}
      {(todo.deal_title || todo.contact_name) && (
        <p className="mt-1 text-xs"><RecordChip todo={todo} /></p>
      )}

      {/* Step 1 — where it's headed. Selecting is local: the item stays put until
          step 3 gives it a context. */}
      <div className="mt-5">
        <h3 className={stepCls}>1. What kind of action?</h3>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {DESTINATIONS.map(d => {
            const on = d.status === destination;
            return (
              <button
                key={d.status}
                type="button"
                disabled={busy}
                aria-pressed={on}
                onClick={() => setDestination(d.status)}
                className={`${destCls} ${on
                  ? 'border-brand bg-brand/10 font-semibold text-charcoal'
                  : 'border-line bg-cream text-muted hover:bg-sand'}`}
              >
                {d.label}
              </button>
            );
          })}
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={() => void patch({ status: 'done' }, true)}
          title="It took under 2 minutes — done"
          className="mt-2 rounded-lg border border-green-600 px-3 py-1.5 text-sm font-heading text-green-700 disabled:opacity-50 dark:text-green-400 hover:bg-green-50 dark:hover:bg-green-950/30"
        >
          Took 2 minutes — Done ✓
        </button>
      </div>

      {/* Step 2 — optional enrichment. Nothing here leaves the inbox. */}
      <div className="mt-5">
        <h3 className={stepCls}>2. Add detail (optional)</h3>
        <div className="flex flex-wrap items-center gap-2">
          {newProject === null ? (
            <select
              value={todo.project_id ?? ''}
              disabled={busy}
              onChange={e => {
                const v = e.target.value;
                if (v === NEW_PROJECT) { setNewProject(''); return; }
                void patch({ project_id: v ? Number(v) : null }, false);
              }}
              className={inputCls}
              aria-label="Project"
            >
              <option value="">No project</option>
              {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
              <option value={NEW_PROJECT}>+ New project…</option>
            </select>
          ) : (
            <form
              onSubmit={e => { e.preventDefault(); void createAndAssign(newProject); }}
              className="flex flex-wrap items-center gap-2"
            >
              <input
                value={newProject}
                onChange={e => setNewProject(e.target.value)}
                placeholder="New project name"
                autoFocus
                disabled={busy}
                className={`${inputCls} border-brand`}
                aria-label="New project name"
              />
              <button
                type="submit"
                disabled={busy || !newProject.trim()}
                className="rounded-lg border border-line bg-cream px-3 py-1.5 text-sm font-heading text-charcoal hover:bg-sand disabled:opacity-50"
              >
                Create
              </button>
              {/* Cancel leaves project_id alone — the select is driven by the todo, so
                  the prior choice comes back untouched. It DOES clear the error: that
                  message belongs to the attempt being abandoned, and leaving it up
                  reads as a failure of whatever comes next. */}
              <button
                type="button"
                disabled={busy}
                onClick={() => { setNewProject(null); setError(''); }}
                className={`${linkCls} text-muted hover:text-charcoal`}
              >
                Cancel
              </button>
            </form>
          )}
          <input
            type="date"
            value={todo.due_date || ''}
            disabled={busy}
            onChange={e => void patch({ due_date: e.target.value || '' }, false)}
            className={inputCls}
            aria-label="Due date"
          />
          <button
            type="button"
            disabled={busy}
            onClick={() => onEdit(current)}
            className={`${linkCls} text-muted hover:text-charcoal`}
          >
            Edit
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void remove()}
            // Underline, not a colour or opacity change (issue #119). `brand-dark` was
            // 2.18:1 as text on the dark card; `hover:opacity-80` — the obvious
            // replacement — is 3.83:1, because fading a token tuned to sit just over
            // 4.5:1 lands under it. A hover affordance that costs no contrast is the
            // only kind these tokens can carry.
            className={`${linkCls} text-ck-accent-text hover:underline`}
          >
            Delete
          </button>
        </div>
      </div>

      {/* Step 3 — the last step. Picking a context files it and clears the inbox. */}
      <div className="mt-5 border-t border-line-faint pt-4">
        <h3 className={stepCls}>3. Last step — set context (required)</h3>
        <p className="mb-2 text-sm text-muted">
          Where can you actually do this? Every item needs a context before it leaves the
          inbox. Choosing one files it under{' '}
          <span className="font-semibold text-charcoal">{dest.list}</span> and clears it out of
          your inbox — do it last.
        </p>
        {newContext === null ? (
          <div className="flex flex-wrap items-center gap-2">
            <select
              value=""
              disabled={busy}
              onChange={e => {
                const v = e.target.value;
                if (!v) return;
                if (v === NEW_CONTEXT) { setNewContext(''); return; }
                const picked = options[Number(v)];
                if (picked) void fileUnder(picked);
              }}
              className={`${inputCls} border-brand`}
              aria-label="Set context and file this todo"
            >
              <option value="">Set context…</option>
              {options.map((c, i) => <option key={c} value={i}>{c}</option>)}
              <option value={NEW_CONTEXT}>+ New context…</option>
            </select>
            {todo.context && <span className="text-xs text-muted">currently {todo.context}</span>}
          </div>
        ) : (
          <form
            onSubmit={e => { e.preventDefault(); void fileUnder(newContext); }}
            className="flex flex-wrap items-center gap-2"
          >
            <input
              value={newContext}
              onChange={e => setNewContext(e.target.value)}
              placeholder="@home, @calls, @errands…"
              autoFocus
              disabled={busy}
              className={`${inputCls} border-brand`}
              aria-label="New context"
            />
            <button
              type="submit"
              disabled={busy || !newContext.trim()}
              className="rounded-lg bg-brand-dark px-4 py-1.5 text-sm font-heading text-white hover:bg-brand-deep disabled:opacity-50"
            >
              File →
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => { setNewContext(null); setError(''); }}
              className={`${linkCls} text-muted hover:text-charcoal`}
            >
              Cancel
            </button>
          </form>
        )}
      </div>
      {error && <p className="mt-3 text-sm text-ck-accent-text">{error}</p>}
    </div>
  );
}
