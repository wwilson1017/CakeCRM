// Todo-GTD — the undo queue (#231, porting cake_os #2879 mark-done + #2925 inbox triage).
//
// TWO kinds of action are undoable and they share one queue, one block and one 7s
// window: marking a todo done, and filing an inbox item under a context (the one write
// that takes an item OUT of the inbox, see `TriageCard.fileUnder`). What separates them
// is only WHAT gets written back, which is why an entry carries a ready-made `restore`
// payload rather than a bare previous status.
//
// A module-level pub/sub store, deliberately the same shape as `shared/toast.ts`: the
// app uses flat sibling routes, so `TodoShell` UNMOUNTS on every tab switch. Component
// state could not survive a navigation, module state can — which is what lets a
// completion on Today still offer its undo after the user has moved to Inbox.
//
// This file is PURE: a queue, timers and listeners, with no API, React-tree or toast
// imports. The write half (re-issuing the PUT, refreshing the lists) lives in `hooks.ts`
// beside the other mutations, which also keeps the dependency one-directional —
// hooks.ts → undoQueue.ts, never back.
//
// Divergence from the blueprint, stated once: upstream stamps every row with an OWNER
// and filters by it, because a BroadcastChannel token adoption there can swap the
// signed-in user inside one tab with no reload. CakeCRM has no in-tab identity swap —
// signing out drops the session and the whole SPA re-renders through /login, and the
// public /todo/{token} surface has no user at all — so the owner scoping collapses to
// "whoever is signed in" and is not ported. The rule that survives is the one that
// still has a producer: at most ONE row per todo id, replaced on re-queue.
import { useSyncExternalStore } from 'react';
import type { Todo } from './types';

/** How long a row stays offering its undo. Per-row and independent: a completion added
 *  later must NOT extend the row already counting down. */
export const UNDO_WINDOW_MS = 7000;

/** What the row is offering to undo. Drives the block's copy, the accessible names,
 *  whether the reopened-a-repeat notice applies, and whether the revert re-surfaces the
 *  item as the inbox's current triage card. */
export type UndoKind = 'done' | 'filed';

/** The fields handed straight back to `updateTodo`. Narrow on purpose: a
 *  `Record<string, unknown>` here would let any field — or an invalid status — through
 *  the one call that is supposed to put the row back exactly. */
export type UndoRestore = Pick<Todo, 'status'> & Partial<Pick<Todo, 'context'>>;

export interface UndoEntry {
  /** Unique per EVENT, not per todo — it is the React key and the handle the timer
   *  closes over. */
  key: number;
  id: number;
  title: string;
  kind: UndoKind;
  /** The row as it was immediately before the write — what undo puts back. 'done'
   *  restores the status alone, and it is not always 'next_action': Inbox triage
   *  completes from 'inbox' and the Done page's Dropped tab from 'dropped'. 'filed'
   *  restores the context TOO, because filing writes both in one go and an item that
   *  arrived carrying "@errands" from Quick Add must go back to '@errands', not to
   *  empty. */
  restore: UndoRestore;
  /** Carried so the undo path can repeat the app's existing "its next occurrence already
   *  exists" notice without a second fetch. The rule the completion ACTUALLY ran under:
   *  the backend spawns from the post-update row, so an edit that sets done and changes
   *  the repeat in one save must stamp the new rule. Only ever consulted for kind
   *  'done' — a filing spawns nothing. */
  repeat: string;
}

let nextKey = 1;
let current: UndoEntry[] = [];
const timers = new Map<number, ReturnType<typeof setTimeout>>();
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach(l => l());
}

function clearTimer(key: number) {
  const t = timers.get(key);
  if (t !== undefined) {
    clearTimeout(t);
    timers.delete(key);
  }
}

/** Queue an undo row for a todo that was JUST written. `todo` must be the row as it was
 *  BEFORE the write — its `status` (and, for a filing, its `context`) is what undo
 *  restores. `kind` defaults to 'done' so every mark-done call site reads plainly. */
export function queueUndo(
  todo: Pick<Todo, 'id' | 'title' | 'status' | 'repeat' | 'context'>,
  kind: UndoKind = 'done',
): UndoEntry {
  const entry: UndoEntry = {
    key: nextKey++,
    id: todo.id,
    title: todo.title,
    kind,
    restore: kind === 'filed'
      ? { status: todo.status, context: todo.context }
      : { status: todo.status },
    repeat: todo.repeat,
  };
  // At most ONE row per todo, whatever the kinds. A todo cannot be completed twice
  // without being un-done in between (which calls `dropUndo`), so a second queue for
  // the same id is always a double-fire: `TodoRow`'s checkbox is controlled but its list
  // does not re-render until the write resolves, so a double-tap re-fires `onChange`
  // with the same pre-write row. Replacing rather than appending keeps the header count
  // honest and stops a duplicate row outliving the undo — and it is also what retires a
  // stale mark-done row when the same todo is then filed, for the same reason `dropUndo`
  // exists: the older offer no longer describes a pending change.
  current = [...current.filter(e => {
    if (e.id !== todo.id) return true;
    clearTimer(e.key);
    return false;
  }), entry];
  timers.set(entry.key, setTimeout(() => {
    // Elapsed with no click: the row silently drops out and the write stands.
    timers.delete(entry.key);
    current = current.filter(e => e.key !== entry.key);
    emit();
  }, UNDO_WINDOW_MS));
  emit();
  return entry;
}

/** Remove one entry and hand it back, or undefined if it already expired or was taken.
 *  Taking BEFORE the revert is issued is what makes a double-click idempotent and stops
 *  the timer racing an in-flight undo. */
export function takeEntry(key: number): UndoEntry | undefined {
  const entry = current.find(e => e.key === key);
  if (!entry) return undefined;
  clearTimer(key);
  current = current.filter(e => e.key !== key);
  emit();
  return entry;
}

/** Drain the whole queue in one shot ("Undo all"). */
export function takeAll(): UndoEntry[] {
  const entries = current;
  entries.forEach(e => clearTimer(e.key));
  current = [];
  if (entries.length > 0) emit();
  return entries;
}

/** Is a FILING row pending for this todo? A filing restores the CONTEXT as well as the
 *  status (see `UndoRestore`), so a later write that changes the context has moved the
 *  todo off where the filing left it and must retire the offer — where that same edit
 *  leaves a mark-done row, which puts back only the status, perfectly valid. Callers
 *  that write a context alongside a status need this to tell the two cases apart; one
 *  that only ever writes a status does not. */
export function hasPendingFiling(id: number): boolean {
  return current.some(e => e.id === id && e.kind === 'filed');
}

/** Drop any queued row for a todo, without reverting anything. Called when a later
 *  write MOVES the todo off where the queued write put it: the offer to "undo" a change
 *  the user has since overridden themselves is a stale affordance, and clicking it would
 *  re-apply a state that is no longer pending. */
export function dropUndo(id: number) {
  const gone = current.filter(e => e.id === id);
  if (gone.length === 0) return;
  gone.forEach(e => clearTimer(e.key));
  current = current.filter(e => e.id !== id);
  emit();
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  return () => { listeners.delete(onChange); };
}

const getSnapshot = () => current;

/** `useSyncExternalStore` rather than a useState+useEffect pair, for a reason specific
 *  to this queue: TodoShell remounts on every tab switch, and a row's 7s timer can
 *  elapse in the gap between that render reading the module and the passive effect
 *  subscribing to it. A seeded useState would then hold a row nothing will ever emit a
 *  correction for — a stuck "Undo?" that reverts a todo the user never asked to revert.
 *  This subscribes before it reads. `current` is replaced, never mutated, so it is a
 *  stable snapshot between emits. */
export function useUndoQueue(): UndoEntry[] {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

/** Drop every entry and its timer. For tests: the queue is module state that
 *  deliberately outlives the page, so without this a row queued by one case is still
 *  offered in the next. */
export function resetUndoQueue() {
  current.forEach(e => clearTimer(e.key));
  current = [];
  emit();
}
