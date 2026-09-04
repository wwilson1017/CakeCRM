import { useLayoutEffect, useMemo, useRef, useState } from 'react';
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

const stepCls = 'mb-2 text-sm font-heading font-bold uppercase tracking-wide text-charcoal';
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
  // A native date input ignores `placeholder`, so step 2's empty-state cue is an overlay
  // that has to know when the field has focus — otherwise it would sit on top of the
  // native editor while a date is being picked.
  const [dueFocused, setDueFocused] = useState(false);
  // Opening the sheet waits on the notes flush, which is a round trip on a slow link.
  const [editPending, setEditPending] = useState(false);
  // The due date this card just wrote, until the parent's reload feeds it back in through
  // `todo` — the same shape as `pendingTitle` above, and needed for the same reason: the
  // input is CONTROLLED by the prop, so without it the field reverts to the old value the
  // moment the write is sent (blanking, with the cue flashing back over it, when there was
  // no date before) for the whole length of the refetch.
  const [pendingDue, setPendingDue] = useState<string | null>(null);
  // What the notes box shows, and the server value it is measured against. Dirty is
  // `notesDraft !== baseNotes`, and the baseline moves the moment a write is ACKNOWLEDGED
  // rather than when the parent's reload lands — deferring it would leave the box reading
  // dirty, and re-sending, for a whole round trip after it was already saved.
  const [baseNotes, setBaseNotes] = useState(todo.notes);
  const [notesDraft, setNotesDraft] = useState(todo.notes);
  const dest = DESTINATIONS.find(d => d.status === destination) ?? DESTINATIONS[0];

  // Take notes that changed underneath this card — the Edit sheet writes them too, and
  // `InboxPage` keys the card by todo id, so a same-id reload does NOT remount it and
  // seeding state once would leave the box showing the pre-sheet text (which the next blur
  // would then write back over the edit). Adopted during render — React's documented
  // adjust-state-during-render, which converges in one extra pass rather than painting the
  // stale value first the way an effect would.
  //
  // Unsaved text of the user's own is never overwritten: it stays on screen and stays dirty.
  //
  // Simplification vs. the blueprint, which orders every adoption by `updated_at`: this card
  // adopts only from the prop, never from a write's own response, so the worst a refetch
  // arriving out of order can do is show an older note in an otherwise CLEAN box for the
  // moment before the newer one lands. Version ordering becomes necessary if responses are
  // ever adopted here too.
  if (todo.notes !== baseNotes) {
    const clean = notesDraft === baseNotes;
    setBaseNotes(todo.notes);
    if (clean) setNotesDraft(todo.notes);
  }

  const dueValue = pendingDue ?? todo.due_date;

  // The notes write in flight, so a second commit queues BEHIND it instead of racing it.
  const notesInFlight = useRef<Promise<boolean> | null>(null);
  // A live view of the values the queued continuation below needs. It runs after further
  // renders, so a plain closure would send text the user has since replaced. A layout
  // effect, so a commit landing right after a render still reads that render's values.
  const notesRef = useRef({ draft: notesDraft, base: baseNotes, id: todo.id });
  useLayoutEffect(() => {
    notesRef.current = { draft: notesDraft, base: baseNotes, id: todo.id };
  });

  /**
   * Commit the notes box if it holds anything new; resolves false only when a write was
   * attempted and failed.
   *
   * Deliberately does NOT take the card-wide `busy`, for the reason `saveTitle` gives
   * below: clicking a button is what blurs the textarea, and a shared flag would swallow
   * that very click. Non-resolving by construction — `onChanged`, never `onProcessed` —
   * so jotting a note can never file the item.
   */
  const flushNotes = (): Promise<boolean> => {
    const send = (): Promise<boolean> => {
      const { draft, base, id } = notesRef.current;
      if (draft === base) return Promise.resolve(true);
      setError('');
      const req = updateTodo(id, { notes: draft })
        .then(() => {
          setBaseNotes(draft);
          onChanged();
          return true;
        })
        .catch((e: unknown) => {
          const msg = e instanceof Error ? e.message : 'Update failed';
          setError(msg);
          // A toast AS WELL, for the reason `createAndAssign` gives below: this card can
          // unmount mid-flight — filing swaps the head item and `InboxPage` keys the card
          // by todo id — and an inline message would land on a dead component and vanish,
          // taking a paragraph the user typed with it.
          toast.error(msg);
          return false;
        })
        .finally(() => { if (notesInFlight.current === req) notesInFlight.current = null; });
      notesInFlight.current = req;
      return req;
    };
    const running = notesInFlight.current;
    // Queue behind an in-flight save rather than racing it: two writes to the same column,
    // in flight together, land in whichever order the server picks. ONE trailing run is
    // enough — `send` reads the LIVE draft, so it carries everything typed since.
    return running ? running.then(send, send) : send();
  };

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
    // A note jotted in step 2 belongs to the same triage gesture, so it is committed WITH
    // the decision: the click that files the item is the very thing that blurs the
    // textarea, and flushing here puts the note on the row before the item leaves the
    // inbox. Cheap when there is nothing pending — it resolves immediately.
    //
    // Its own write rather than folded into `fields`, and deliberately NOT gated on the
    // result. Filing is this card's one exit — a note the server keeps rejecting (20k
    // characters, say) would otherwise trap the item in the inbox forever. The failure is
    // already said twice, inline and as a toast.
    const notesOk = resolves ? await flushNotes() : true;
    if (notesOk) setError('');
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

  // What the sheet is handed: this card's best view of the row, never the lagging prop.
  // All three of these can be ahead of `todo` while a refetch is in flight, and the sheet
  // writes back every field it is given — so passing the prop would silently revert a
  // rename, a date, or a paragraph the user just typed. (Star and project are absent
  // because they are written straight through `patch` and never rendered optimistically,
  // so the prop is the only view of them this card has.)
  const current: Todo = {
    ...todo,
    title: pendingTitle ?? todo.title,
    notes: notesDraft,
    due_date: dueValue,
  };

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
            todo.star ? 'text-amber-500' : 'text-line hover:text-amber-400'
          }`}
          aria-label="Star as today priority"
        >
          ★
        </button>
      </div>
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
          {/* A native date input ignores `placeholder` — browsers render their own empty
              state instead — so the "Add due date" cue is an opaque overlay laid over the
              whole control, exactly as a real placeholder would fill the field. It clears
              as soon as there is a value or the field takes focus, so the native editor
              and its calendar button are never covered while in use. `inset-px` covers the
              control in every engine (WebKit renders a narrow box showing today's date
              greyed rather than mm/dd/yyyy, so a partial overlay leaves digits peeking
              out), and the width floor keeps the cue on one line. */}
          <span className="relative inline-flex">
            <input
              type="date"
              value={dueValue}
              disabled={busy}
              onFocus={() => setDueFocused(true)}
              onBlur={() => setDueFocused(false)}
              onChange={e => {
                const picked = e.target.value;
                // The write below sets `busy`, which DISABLES this input — and React does
                // not dispatch to a disabled target, so `onBlur` never runs and the flag
                // would strand `true` for the life of the card. (The browser does fire
                // blur; React simply declines to call the handler. There is no browser
                // quirk to go looking for.) A stranded `true` is invisible while a date is
                // set, because `dueValue` hides the cue on its own — and then bites the
                // moment the date is cleared: an empty box, the cue suppressed by a focus
                // that ended long ago, and the user back to a bare `mm/dd/yyyy`.
                setDueFocused(false);
                setPendingDue(picked);
                // Reverted on failure, exactly like `pendingTitle`: nothing was written, so
                // the field must not go on showing a date the server never took.
                void patch({ due_date: picked }, false).then(ok => { if (!ok) setPendingDue(null); });
              }}
              className={`${inputCls} min-w-40`}
              aria-label="Due date"
            />
            {/* Keyed off `dueValue`, not the raw prop: without the optimistic value the
                field reverts the instant the write is sent and the cue flashes back over
                the date just chosen, for the whole length of the refetch. */}
            {!dueValue && !dueFocused && (
              <span
                aria-hidden="true"
                className="pointer-events-none absolute inset-px flex items-center whitespace-nowrap rounded-lg bg-cream pl-2 text-sm text-muted"
              >
                Add due date
              </span>
            )}
          </span>
          <button
            type="button"
            disabled={busy || editPending}
            onClick={() => {
              // Clicking Edit is what blurs the textarea, so a notes write is already in
              // flight — and the sheet writes `notes` too. Without ordering them this
              // card's older PUT can land after the sheet's Save and overwrite it. Opened
              // regardless of the result: a draft the server rejected is exactly what the
              // sheet is there to rescue, and `current` hands it over either way.
              setEditPending(true);
              void flushNotes().then(() => {
                setEditPending(false);
                onEdit(current);
              });
            }}
            className={`${linkCls} text-muted hover:text-charcoal`}
          >
            Edit
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void remove()}
            className={`${linkCls} text-ck-accent-text hover:text-brand-dark`}
          >
            Delete
          </button>
        </div>
        {/* Notes belong to clarifying, not only to the full editor — jot the link or the
            phone number without leaving triage. Saved on blur, non-resolving, so the item
            stays in the inbox. This replaces the read-only preview that used to sit under
            the title. */}
        <textarea
          value={notesDraft}
          disabled={busy}
          onChange={e => setNotesDraft(e.target.value)}
          onBlur={() => void flushNotes()}
          placeholder="Notes — links, numbers, anything you'll want when you do it"
          className={`${inputCls} mt-2 block min-h-16 w-full`}
          aria-label="Notes"
        />
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
