import { useCallback, useLayoutEffect, useMemo, useReducer, useRef, useState } from 'react';
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

/** The sub-millisecond part of an ISO timestamp, in microseconds within the millisecond.
 * `Date.parse` truncates at the millisecond, and every task write stamps `updated_at` from
 * `datetime.now(timezone.utc).isoformat()` — microseconds. This is the precision it threw
 * away. Read out of the fractional field alone, so it does not care whether the zone is
 * written `+00:00` or `Z`; a lexical compare of the whole string would, since `Z` sorts
 * after `+`. */
/**
 * Serialize one field's commits: at most one write in flight, and one trailing run behind it.
 *
 * `send` reads LIVE state, so whichever continuation runs first carries everything typed since
 * and the rest find nothing to do. Queueing on the TAIL rather than on the request in flight is
 * what keeps a third caller from waking alongside the second and firing a duplicate.
 *
 * Two fields need this and they need it for the same reason: two writes to one column, in
 * flight together, land in whichever order the server picks.
 */
function useSerialCommit<T>(send: () => Promise<T>): () => Promise<T> {
  const inFlight = useRef<Promise<T> | null>(null);
  // A live view of `send`, which closes over the render that created it.
  const sendRef = useRef(send);
  useLayoutEffect(() => { sendRef.current = send; });
  return useCallback((): Promise<T> => {
    const run = () => sendRef.current();
    const tail = inFlight.current;
    const next = tail ? tail.then(run, run) : run();
    inFlight.current = next;
    // Let the chain be garbage once it has drained, so an idle field starts a fresh one
    // rather than accumulating continuations for the life of the session.
    const release = () => { if (inFlight.current === next) inFlight.current = null; };
    void next.then(release, release);
    return next;
  }, []);
}

const subMs = (iso: string): number => {
  const frac = /\.(\d+)/.exec(iso);
  return frac ? Number(frac[1].slice(3, 6).padEnd(3, '0')) : 0;
};

/**
 * Is row version `a` strictly newer than `b`?
 *
 * Every task write bumps `updated_at` (`service._apply_task_update_cur`), so this orders
 * every view of the record the card can be handed. Two commits inside the same millisecond
 * would compare equal on `Date.parse` alone — and equal means "reject", which would drop the
 * newer row and leave the card on the older one for good — so the tie falls to the
 * microseconds `Date.parse` discarded.
 *
 * An unparseable value is never newer: a deliberate floor, not a live path.
 */
const isNewer = (a: string, b: string): boolean => {
  const na = Date.parse(a);
  const nb = Date.parse(b);
  if (Number.isNaN(na) || Number.isNaN(nb)) return false;
  return na !== nb ? na > nb : subMs(a) > subMs(b);
};

/**
 * Everything about this card that a WRITE can move, in one reducer.
 *
 * A reducer rather than a handful of `useState`s because most of it is touched from
 * asynchronous callbacks. A callback created during one render closes over that render's
 * values, so state read inside it can be arbitrarily old — and here that is data loss, not
 * just staleness: a title save resolving after the user has started typing notes would
 * compare against the empty draft it captured, decide the box was clean, and overwrite what
 * was typed. Every decision below is made against the state as it is NOW.
 */
interface CardState {
  /** The record as this card best knows it — never the lagging `todo` prop. */
  row: Todo;
  /** Dirty is `notesDraft !== row.notes`: the row IS the baseline, because the only thing
   * that moves it is adopting a newer row, and a newer row is by definition what the server
   * holds. */
  notesDraft: string;
  /** Optimistic values, shadowing the row until it catches up. null = not overriding. */
  pendingDue: string | null;
  pendingTitle: string | null;
}

type CardAction =
  /** A newer view of the row, from the parent's prop or a write's own response. */
  | { type: 'adopt'; row: Todo }
  | { type: 'notes-draft'; value: string }
  | { type: 'due'; value: string | null }
  | { type: 'title'; value: string | null };

/**
 * Take a newer view of the row. An older or equal one is ignored: that is this card's own
 * write echoing back off a read taken before it committed, and adopting it is what would
 * rewind the notes box the instant a save succeeded.
 *
 * Unsaved text of the user's own is never overwritten — it stays on screen and stays dirty.
 *
 * Adoption deliberately does NOT touch the optimistic overrides. Each is released when its
 * OWN write settles, because a row is not evidence about a write still in flight: releasing
 * one because the adopted row disagrees with it cannot tell "someone changed this elsewhere"
 * from "this row was committed before my write was". An earlier write of this card's own,
 * answering first, carries exactly that disagreement — and releasing on it flashes the field
 * back to the value the override exists to hide, and hands the Edit sheet the old value,
 * whose full-row save then reverts the change.
 */
function adopt(s: CardState, r: Todo): CardState {
  if (!isNewer(r.updated_at, s.row.updated_at)) return s;
  return {
    ...s,
    row: r,
    notesDraft: s.notesDraft === s.row.notes ? r.notes : s.notesDraft,
  };
}

function reduce(s: CardState, a: CardAction): CardState {
  switch (a.type) {
    case 'adopt':
      return adopt(s, a.row);
    case 'notes-draft':
      return s.notesDraft === a.value ? s : { ...s, notesDraft: a.value };
    case 'due':
      return s.pendingDue === a.value ? s : { ...s, pendingDue: a.value };
    case 'title':
      return s.pendingTitle === a.value ? s : { ...s, pendingTitle: a.value };
  }
}

const initState = (todo: Todo): CardState => ({
  row: todo,
  notesDraft: todo.notes,
  pendingDue: null,
  pendingTitle: null,
});

/**
 * GTD triage — one inbox item at a time, as three deliberate clarify steps: what kind
 * of action it is, optional detail, then the context that files it. Setting a context
 * is the ONLY way out of the inbox.
 */
export function TriageCard({ todo, projects, contexts, onProcessed, onChanged, onEdit }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [destination, setDestination] = useState<TodoStatus>('next_action');
  // null = the picker is showing; a string = the inline create input is.
  const [newContext, setNewContext] = useState<string | null>(null);
  const [newProject, setNewProject] = useState<string | null>(null);
  // A native date input ignores `placeholder`, so step 2's empty-state cue is an overlay
  // that has to know when the field has focus — otherwise it would sit on top of the
  // native editor while a date is being picked.
  const [dueFocused, setDueFocused] = useState(false);
  // Opening the sheet waits on the notes flush, which is a round trip on a slow link.
  const [editPending, setEditPending] = useState(false);
  // Everything a write can move, in one reducer — see `CardState` for why a reducer and not
  // a handful of `useState`s.
  const [state, dispatch] = useReducer(reduce, todo, initState);
  // The SAME state, maintained synchronously. `dispatch` only schedules a render, so an
  // asynchronous continuation that runs before React commits would otherwise read the state
  // as it was when the request began. Every write goes through `apply`, which advances this
  // ref with the identical pure reducer and then dispatches, so the two cannot drift.
  const stateRef = useRef(state);
  const apply = useCallback((a: CardAction) => {
    stateRef.current = reduce(stateRef.current, a);
    dispatch(a);
  }, []);

  // The parent's refetch. A layout effect rather than an adjust-state-during-render block:
  // `apply` writes a ref, and writing a ref during render is the one thing that pattern
  // cannot do. The cost is a frame — and normally not even that, because the row this card
  // already holds is usually its OWN write, which is newer than the prop, so `adopt` returns
  // the same state and React bails out of the re-render entirely. `InboxPage` keys the card
  // by todo id, so a same-id reload does not remount it: this is the only way a change from
  // elsewhere (the Edit sheet, most often) gets in.
  useLayoutEffect(() => { apply({ type: 'adopt', row: todo }); }, [todo, apply]);

  const { row, notesDraft, pendingDue, pendingTitle } = state;
  const dest = DESTINATIONS.find(d => d.status === destination) ?? DESTINATIONS[0];
  const dueValue = pendingDue ?? row.due_date;

  // What the Edit sheet is handed: this card's best view of the record, never the lagging
  // prop. Built from `stateRef` at the moment it is needed rather than captured at click
  // time, because opening waits on the notes flush — a round trip, during which the textarea
  // stays editable AND the flush's own response can carry a star or project someone else
  // changed. The sheet writes back every field it is given, so either kind of staleness is a
  // silent revert.
  const payload = (): Todo => {
    const live = stateRef.current;
    return {
      ...live.row,
      title: live.pendingTitle ?? live.row.title,
      notes: live.notesDraft,
      due_date: live.pendingDue ?? live.row.due_date,
    };
  };

  // Opening the sheet waits on the notes write, and nothing gates the inbox queue meanwhile —
  // promoting another row unmounts this card while the flush is still running, and the
  // continuation would then open the sheet on the todo the user just navigated away from.
  const mounted = useRef(true);
  // The setup half is not ceremony: StrictMode runs setup → cleanup → setup on mount, so a
  // cleanup-only effect would leave this false for the life of the card and the Edit button
  // would flush the notes and then silently do nothing, in every dev run.
  useLayoutEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  /**
   * Commit the notes box if it holds anything new; resolves false only when a write was
   * attempted and failed.
   *
   * Deliberately does NOT take the card-wide `busy`, for the reason `saveTitle` gives below:
   * clicking a button is what blurs the textarea, and a shared flag would swallow that very
   * click. Non-resolving by construction — `onChanged`, never `onProcessed` — so jotting a
   * note can never file the item.
   */
  const flushNotes = useSerialCommit(async (): Promise<boolean> => {
    const { notesDraft: draft, row: live } = stateRef.current;
    if (draft === live.notes) return true;
    setError('');
    try {
      // Just an adoption — the response is the authoritative row, which stops a slow refetch
      // dispatched by an earlier write from reverting what this card has since written, and
      // carries any field someone else changed meanwhile.
      //
      // Deliberately NO "the write was acknowledged, so trust the text over the version" rule.
      // `_now()` is stamped under the row's own `FOR UPDATE` lock
      // (`service._apply_task_update_cur`), so `updated_at` is monotonic PER ROW: a held row
      // newer than this response was committed AFTER our write. Either it already carries our
      // text, making such a rule a no-op, or a later write replaced ours — and there, marking
      // the box clean would strand a paragraph the server does not have, silently and with no
      // error. Leaving it dirty re-sends it, the same last-write-wins rule `adopt` follows.
      apply({ type: 'adopt', row: await updateTodo(live.id, { notes: draft }) });
      onChanged();
      return true;
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Update failed';
      setError(msg);
      // A toast AS WELL, for the reason `createAndAssign` gives below: this card can unmount
      // mid-flight — filing swaps the head item and `InboxPage` keys the card by todo id —
      // and an inline message would land on a dead component and vanish, taking a paragraph
      // the user typed with it.
      toast.error(msg);
      return false;
    }
  });

  /**
   * Commit the due-date field if it holds something new.
   *
   * On BLUR, not on change, and its own write rather than `patch`'s — both for one reason. A
   * date input reports a COMPLETE value the moment every segment parses, so it emits one on
   * nearly every keystroke: typing "12/24/2026" into an empty box yields `0002-12-24` after
   * the year's first digit, and into a populated one yields `2026-01-01` after the month's.
   * `patch` sets `busy`, which DISABLES this input, so that first write ate every remaining
   * keystroke and the truncated date was what reached the server — silently, and measured on
   * the real app at every typing speed from 0 to 300ms per key. Committing on blur means the
   * field is never disabled while it still has focus. Picking from the calendar commits on the
   * blur that follows, and a resolving write flushes it first, so filing carries the date.
   */
  const commitDue = useSerialCommit(async (): Promise<void> => {
    const { pendingDue: pending, row: live } = stateRef.current;
    if (pending === null || pending === live.due_date) return;
    setError('');
    try {
      apply({ type: 'adopt', row: await updateTodo(live.id, { due_date: pending }) });
      onChanged();
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Update failed';
      setError(msg);
      toast.error(msg); // same unmount reasoning as the notes save
    } finally {
      // Released either way: on success the adopted response carries the date, and on failure
      // the field must not go on showing one the server never took.
      apply({ type: 'due', value: null });
    }
  });

  // An inbox item can already carry a context (quick-add parses "@ctx" while keeping
  // status: inbox), and the shared meta can still be empty while it loads — offer the
  // item's own context either way, so the card always has a way out that isn't
  // "retype what is already there".
  const options = useMemo(() => {
    // Trimmed and de-duplicated. The shared meta is a list of values other rows carry, so a
    // legacy row with stray whitespace — or two rows differing only by it — would otherwise
    // put a near-duplicate in the picker and give two options the same React key.
    const known = [...new Set(contexts.map(c => c.trim()).filter(Boolean))];
    const own = row.context.trim();
    return own && !known.includes(own) ? [own, ...known] : known;
  }, [contexts, row.context]);

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
    try {
      const notesOk = resolves ? await flushNotes() : true;
      // The date too, so filing an item carries a date just picked. Not gated on its result
      // for the same reason the note is not: filing is this card's one exit.
      if (resolves) await commitDue();
      if (notesOk) setError('');
      // Adopt the response for the same reason the notes save does. Star and project are
      // written straight through here and never rendered optimistically, so this is the ONLY
      // thing that keeps them current for `payload()` — without it the Edit sheet can open
      // on the pre-write values once `busy` clears but before the refetch lands, and its
      // full-row save reverts them.
      apply({ type: 'adopt', row: await updateTodo(todo.id, fields) });
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
      // Adopted like every other write on this card: `setBusy(false)` runs before the
      // parent's reload resolves, so without this the Edit sheet can open on the pre-
      // assignment `project_id` and its full-row save undoes the assignment.
      apply({ type: 'adopt', row: await updateTodo(todo.id, { project_id: created.id }) });
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
    apply({ type: 'title', value: title });
    try {
      apply({ type: 'adopt', row: await updateTodo(todo.id, { title }) });
      onChanged();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Update failed');
      return false;
    } finally {
      // Released here, where this write settles — the one moment that IS evidence about it.
      // On success the response just adopted carries the new title, so the override has
      // nothing left to add; on failure nothing was written and it must not go on showing a
      // name the server never took. Either way the card falls back to the row it holds.
      apply({ type: 'title', value: null });
    }
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
          title={pendingTitle ?? row.title}
          label="Todo title"
          disabled={busy}
          onSave={saveTitle}
          className="font-heading text-lg font-semibold text-charcoal"
        />
        <button
          type="button"
          disabled={busy}
          onClick={() => void patch({ star: !row.star }, false)}
          className={`shrink-0 text-xl leading-none disabled:opacity-50 ${
            row.star ? 'text-ck-amber-text' : 'text-line hover:text-ck-amber-text'
          }`}
          aria-label="Star as today priority"
        >
          ★
        </button>
      </div>
      {(row.deal_title || row.contact_name) && (
        <p className="mt-1 text-xs"><RecordChip todo={row} /></p>
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
              value={row.project_id ?? ''}
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
              onBlur={() => { setDueFocused(false); void commitDue(); }}
              // Shown immediately, written on blur — `commitDue` says why. Nothing here
              // disables the input, so the user can finish typing the date.
              onChange={e => apply({ type: 'due', value: e.target.value })}
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
              // sheet is there to rescue, and `payload()` hands it over either way.
              setEditPending(true);
              void flushNotes().then(() => {
                if (!mounted.current) return;
                setEditPending(false);
                onEdit(payload());
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
        {/* Notes belong to clarifying, not only to the full editor — jot the link or the
            phone number without leaving triage. Saved on blur, non-resolving, so the item
            stays in the inbox. This replaces the read-only preview that used to sit under
            the title. */}
        <textarea
          value={notesDraft}
          disabled={busy}
          onChange={e => apply({ type: 'notes-draft', value: e.target.value })}
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
            {row.context && <span className="text-xs text-muted">currently {row.context}</span>}
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
