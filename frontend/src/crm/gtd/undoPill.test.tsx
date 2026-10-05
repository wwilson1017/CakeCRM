// @vitest-environment jsdom
//
// #231 — the undo pill (port of cake_os #2879 mark-done + #2925 inbox filing).
//
// Three things here are easy to get wrong and silent when they break, which is
// why each has its own case:
//
//  1. The EMPTY → 1 transition. Will's prototype had exactly this defect: the
//     first completion did not appear until a second one arrived, at which point
//     both showed at once. The issue calls it out as a glitch not to reproduce.
//  2. Per-row timers must be INDEPENDENT. A shared timer is the natural shortcut
//     and its symptom — a later completion silently extending an earlier row's
//     window — never throws.
//  3. Undo must restore the status the row ACTUALLY held, not `next_action`.
//     Every fixture below sits at `someday_maybe` on purpose: a hardcoded
//     default would pass a `next_action` fixture and corrupt real data.
import { act, useSyncExternalStore } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const {
  updateTodoMock, refreshMetaMock, deleteTodoMock, listTodosMock, listProjectsMock,
} = vi.hoisted(() => ({
  updateTodoMock: vi.fn(),
  refreshMetaMock: vi.fn(),
  deleteTodoMock: vi.fn(),
  listTodosMock: vi.fn(),
  listProjectsMock: vi.fn(),
}));
// Wide enough that the REAL TriageCard and TodoEditSheet can be rendered below —
// the other two mark-done call sites are only worth testing through the actual
// components, since the bug they guard against is "somebody removed the line".
vi.mock('./api', () => ({
  updateTodo: updateTodoMock,
  createTodo: vi.fn(),
  deleteTodo: deleteTodoMock,
  createProject: vi.fn(),
  listTodos: listTodosMock,
  listProjects: listProjectsMock,
  getReviewStatus: vi.fn().mockResolvedValue({ review_due: false, days_since_review: 1 }),
  markReviewDone: vi.fn(),
}));
// No owner scoping here (see undoQueue.ts): CakeCRM has no in-tab identity swap, so the
// blueprint's `useTodoOwner` and its owner test have nothing to port.
vi.mock('./useTodoMeta', () => ({
  refreshMeta: refreshMetaMock,
  useTodoMeta: () => ({ filters: null, projects: [], loaded: false, refreshMeta: refreshMetaMock }),
  useListTz: () => undefined,
}));

import { MemoryRouter } from 'react-router-dom';
import { ReviewPage } from './ReviewPage';
import { TriageCard } from './components/TriageCard';
import { TodoEditSheet } from './components/TodoEditSheet';
import { UndoPill } from './components/UndoPill';
import { resetInboxFocus, undoOne, useInboxFocus, useRowActions, useTodosChanged } from './hooks';
import { _resetForTesting as resetToasts, getToasts, subscribeToasts } from '../../shared/toast';
import type { Todo } from './types';
import { UNDO_WINDOW_MS, queueUndo, resetUndoQueue } from './undoQueue';

const BASE: Todo = {
  id: 1, title: 'water the plants', notes: '', project_id: null, project_name: null,
  context: '', tags: [], status: 'someday_maybe', star: false, due_date: '',
  repeat: '', auto_star_on_due: false, source: 'ui',
  created_at: '2026-09-14T12:00:00Z', updated_at: '2026-09-14T12:00:00Z', completed_at: null,
  contact_id: null, deal_id: null,
};

/** The app-wide toast store, rendered where a harness can assert on what the user is
 *  told — `ToastViewport` itself lives in the CRM shell, not in the todo tree. */
const useToasts = () => useSyncExternalStore(subscribeToasts, getToasts, getToasts);
const todo = (over: Partial<Todo> = {}): Todo => ({ ...BASE, ...over });

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-09-24T17:00:00Z'));
  resetUndoQueue();
  resetInboxFocus();
  resetToasts();
  updateTodoMock.mockReset().mockResolvedValue(BASE);
  refreshMetaMock.mockReset();
  deleteTodoMock.mockReset().mockResolvedValue(undefined);
  listTodosMock.mockReset().mockResolvedValue([]);
  listProjectsMock.mockReset().mockResolvedValue([]);
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  resetUndoQueue();
  sessionStorage.clear();
  vi.useRealTimers();
});

const rows = () => [...container.querySelectorAll('li')];
const pill = () => container.querySelector('[aria-label="Recent changes you can undo"]');
const headerCount = () => pill()?.querySelector('span')?.textContent?.trim() ?? null;

function button(label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll('button')]
    .find(b => b.textContent?.trim() === label);
  if (!found) throw new Error(`no button labelled "${label}"`);
  return found;
}
const findButton = (label: string) =>
  [...container.querySelectorAll('button')].find(b => b.textContent?.trim() === label);

/** Undo buttons all read "Undo?", so rows are addressed by their accessible name —
 *  which is also what pins the two kinds apart: a filing and a completion undo
 *  different things and must not claim to undo the same one. */
function undoFor(title: string): HTMLButtonElement {
  const found = container.querySelector<HTMLButtonElement>(
    `button[aria-label="Undo marking “${title}” done"]`,
  );
  if (!found) throw new Error(`no undo button for "${title}"`);
  return found;
}
function undoFiledFor(title: string): HTMLButtonElement {
  const found = container.querySelector<HTMLButtonElement>(
    `button[aria-label="Undo filing “${title}”"]`,
  );
  if (!found) throw new Error(`no filing-undo button for "${title}"`);
  return found;
}

/** React tracks the value it last rendered, so a plain `el.value = x` is invisible
 *  to it — go through the prototype setter the way React's own tracker does. */
function setValue(el: HTMLSelectElement | HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')?.set;
  act(() => {
    setter?.call(el, value);
    el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
  });
}

const renderPill = () => act(() => { root.render(<UndoPill />); });
const advance = (ms: number) => act(() => { vi.advanceTimersByTime(ms); });
const clickAsync = async (el: Element) => {
  await act(async () => { (el as HTMLElement).click(); });
};

describe('UndoPill', () => {
  it('appears on the very first completion, with one row and a count of 1', () => {
    renderPill();
    expect(pill()).toBeNull();

    act(() => { queueUndo(todo({ id: 7, title: 'call the vet' })); });

    expect(pill()).not.toBeNull();
    expect(rows()).toHaveLength(1);
    expect(rows()[0].textContent).toContain('call the vet');
    expect(headerCount()).toBe('1 marked done');
  });

  it('shows Undo all only once a second row is queued', () => {
    renderPill();
    act(() => { queueUndo(todo({ id: 1, title: 'one' })); });
    expect(findButton('Undo all')).toBeUndefined();

    act(() => { queueUndo(todo({ id: 2, title: 'two' })); });

    expect(headerCount()).toBe('2 marked done');
    expect(findButton('Undo all')).toBeDefined();
  });

  it('gives each row its own timer — a later completion never extends an earlier one', () => {
    renderPill();
    act(() => { queueUndo(todo({ id: 1, title: 'first' })); });
    advance(3000);
    act(() => { queueUndo(todo({ id: 2, title: 'second' })); });

    // t = 3s: both alive.
    expect(rows()).toHaveLength(2);

    // t = 7s: the first row's window is up; the second still has 3s left.
    advance(UNDO_WINDOW_MS - 3000);
    expect(rows().map(r => r.textContent)).toHaveLength(1);
    expect(rows()[0].textContent).toContain('second');

    // t = 10s: the second row's own 7s elapses and the block disappears.
    advance(3000);
    expect(pill()).toBeNull();
  });

  it('restores the status the row actually held, and drops only that row', async () => {
    renderPill();
    act(() => {
      queueUndo(todo({ id: 11, title: 'keep me', status: 'waiting_for' }));
      queueUndo(todo({ id: 12, title: 'put me back', status: 'someday_maybe' }));
    });

    await clickAsync(undoFor('put me back'));

    expect(updateTodoMock).toHaveBeenCalledTimes(1);
    expect(updateTodoMock).toHaveBeenCalledWith(12, { status: 'someday_maybe' });
    expect(rows()).toHaveLength(1);
    expect(rows()[0].textContent).toContain('keep me');
  });

  it('Undo all reverts every queued row to its own prior status', async () => {
    renderPill();
    act(() => {
      queueUndo(todo({ id: 21, title: 'a', status: 'inbox' }));
      queueUndo(todo({ id: 22, title: 'b', status: 'delegated' }));
    });

    await clickAsync(button('Undo all'));

    expect(updateTodoMock).toHaveBeenCalledWith(21, { status: 'inbox' });
    expect(updateTodoMock).toHaveBeenCalledWith(22, { status: 'delegated' });
    expect(pill()).toBeNull();
  });

  it('ignores a second click on a row whose undo is already in flight', async () => {
    renderPill();
    act(() => { queueUndo(todo({ id: 31, title: 'double' })); });
    const btn = undoFor('double');

    await clickAsync(btn);
    await clickAsync(btn); // the node is detached by now; clicking it must be inert

    expect(updateTodoMock).toHaveBeenCalledTimes(1);
  });

  it('keeps at most one row per todo, so a double-fired completion cannot duplicate it', () => {
    renderPill();
    const item = todo({ id: 61, title: 'double tapped', status: 'waiting_for' });

    // What a double-tapped checkbox does: the list has not reloaded, so the
    // second onChange arrives carrying the same pre-write row.
    act(() => { queueUndo(item); queueUndo(item); });

    expect(rows()).toHaveLength(1);
    expect(headerCount()).toBe('1 marked done');
  });

  it('drops the row when its 7s elapses, leaving the todo done', () => {
    renderPill();
    act(() => { queueUndo(todo({ id: 41, title: 'expire me' })); });

    advance(UNDO_WINDOW_MS);

    expect(pill()).toBeNull();
    expect(updateTodoMock).not.toHaveBeenCalled();
  });
});

/** The checkbox path — `useRowActions.toggleDone` is the one shared by Today, To Do,
 *  Someday, Done, Contexts and Project detail, so this covers six surfaces at once. */
function ToggleHarness({ item }: { item: Todo }) {
  const { toggleDone } = useRowActions(() => {});
  return (
    <>
      <button type="button" onClick={() => void toggleDone(item)}>toggle</button>
      <UndoPill />
    </>
  );
}

describe('toggleDone ↔ the undo queue', () => {
  it('queues a row when marking done, carrying the prior status', async () => {
    const item = todo({ id: 51, title: 'mow the lawn', status: 'someday_maybe' });
    await act(async () => { root.render(<ToggleHarness item={item} />); });

    await clickAsync(button('toggle'));

    expect(updateTodoMock).toHaveBeenCalledWith(51, { status: 'done' });
    expect(rows()).toHaveLength(1);
    await clickAsync(undoFor('mow the lawn'));
    expect(updateTodoMock).toHaveBeenLastCalledWith(51, { status: 'someday_maybe' });
  });

  it('drops the queued row when the user un-checks the box themselves', async () => {
    const item = todo({ id: 52, title: 'stale offer', status: 'next_action' });
    await act(async () => { root.render(<ToggleHarness item={item} />); });
    await clickAsync(button('toggle'));
    expect(rows()).toHaveLength(1);

    // Re-render as the DONE row the list would now show, and un-check it.
    const done = todo({ id: 52, title: 'stale offer', status: 'done' });
    await act(async () => { root.render(<ToggleHarness item={done} />); });
    await clickAsync(button('toggle'));

    expect(pill()).toBeNull();
  });
});

describe('the other two mark-done surfaces', () => {
  // Rendered for real rather than asserted on the call site, because the failure
  // this guards is "someone deleted the queueUndo line" — which only a test that
  // drives the actual button can see. Both were mutation-checked: removing either
  // line leaves every other suite in this app green.

  it('Inbox triage’s “Took 2 minutes — Done ✓” queues a row that undoes back to inbox', async () => {
    const item = todo({ id: 71, title: 'dentist', status: 'inbox' });
    updateTodoMock.mockResolvedValue({ ...item, status: 'done' });
    await act(async () => {
      root.render(
        <>
          <TriageCard todo={item} projects={[]} contexts={[]}
                      onProcessed={() => {}} onChanged={() => {}} onEdit={() => {}} />
          <UndoPill />
        </>,
      );
    });

    await clickAsync(button('Took 2 minutes — Done ✓'));

    expect(updateTodoMock).toHaveBeenCalledWith(71, { status: 'done' });
    expect(rows()).toHaveLength(1);

    await clickAsync(undoFor('dentist'));
    expect(updateTodoMock).toHaveBeenLastCalledWith(71, { status: 'inbox' });
  });

  it('the edit sheet’s Status dropdown queues a row that undoes to the prior status', async () => {
    const item = todo({ id: 72, title: 'renew passport', status: 'waiting_for' });
    updateTodoMock.mockResolvedValue({ ...item, status: 'done' });
    await act(async () => {
      root.render(
        <>
          <TodoEditSheet todo={item} projects={[]} contexts={[]}
                         onClose={() => {}} onSaved={() => {}} />
          <UndoPill />
        </>,
      );
    });

    const status = container.querySelector<HTMLSelectElement>('#gtd-status');
    if (!status) throw new Error('no status select');
    setValue(status, 'done');
    await clickAsync(button('Save'));

    expect(rows()).toHaveLength(1);
    await clickAsync(undoFor('renew passport'));
    expect(updateTodoMock).toHaveBeenLastCalledWith(72, { status: 'waiting_for' });
  });
});

describe('a pending undo is invalidated by any later status write', () => {
  // Codex caught this at stage 2: the undo row is only valid while the todo is
  // still in the state mark-done put it in. Reachable because Search keeps
  // completed rows on screen, so the edit sheet opens inside the 7s window.

  it('the edit sheet moving a done todo elsewhere drops its pending undo', async () => {
    const item = todo({ id: 73, title: 'moved on', status: 'someday_maybe' });
    updateTodoMock.mockResolvedValue({ ...item, status: 'done' });

    // Completed from a list a moment ago...
    act(() => { queueUndo(item) });

    // ...then deliberately refiled through the sheet, still inside the window.
    await act(async () => {
      root.render(
        <>
          <TodoEditSheet todo={{ ...item, status: 'done' }} projects={[]} contexts={[]}
                         onClose={() => {}} onSaved={() => {}} />
          <UndoPill />
        </>,
      );
    });
    const status = container.querySelector<HTMLSelectElement>('#gtd-status');
    if (!status) throw new Error('no status select');
    setValue(status, 'waiting_for');
    await clickAsync(button('Save'));

    // The offer is gone; nothing can now overwrite the status just chosen.
    expect(pill()).toBeNull();
  });

  it('setStatus drops a pending undo for the same todo', async () => {
    const item = todo({ id: 74, title: 'reactivated', status: 'someday_maybe' });
    await act(async () => { root.render(<SetStatusHarness item={item} />); });
    act(() => { queueUndo(item); });
    expect(rows()).toHaveLength(1);

    await clickAsync(button('to next'));

    expect(pill()).toBeNull();
  });
});

/** Waiting's "-> Next" button, the one caller of `useRowActions.setStatus`. */
function SetStatusHarness({ item }: { item: Todo }) {
  const { setStatus } = useRowActions(() => {});
  return (
    <>
      <button type="button" onClick={() => void setStatus(item, 'next_action')}>to next</button>
      <UndoPill />
    </>
  );
}

describe('settle-stage fixes', () => {
  it('Review takes the undo broadcast too, despite owning its own fetch', async () => {
    await act(async () => {
      root.render(<MemoryRouter><ReviewPage /></MemoryRouter>);
    });
    const initial = listTodosMock.mock.calls.length;
    expect(initial).toBeGreaterThan(0);

    act(() => { queueUndo(todo({ id: 91, title: 'a next action' })); });
    await clickAsync(undoFor('a next action'));

    // Undone from Review, the project it belonged to must stop being listed as
    // having no next action — which only happens if the page refetches.
    expect(listTodosMock.mock.calls.length).toBeGreaterThan(initial);
  });

  it('records the repeat rule the completion actually ran under, not the pre-edit one', async () => {
    // Setting done and ADDING recurrence in one save: the backend spawns from the
    // post-update row, so undo must warn about the successor it really created.
    const item = todo({ id: 92, title: 'water the plants', status: 'next_action', repeat: '' });
    updateTodoMock.mockResolvedValue({ ...item, status: 'done', repeat: 'weekly' });
    await act(async () => {
      root.render(
        <>
          <TodoEditSheet todo={item} projects={[]} contexts={[]}
                         onClose={() => {}} onSaved={() => {}} />
          <BroadcastHarness onReload={() => {}} />
        </>,
      );
    });
    const repeatSel = container.querySelector<HTMLSelectElement>('#gtd-repeat');
    if (!repeatSel) throw new Error('no repeat select');
    setValue(repeatSel, 'weekly');
    const statusSel = container.querySelector<HTMLSelectElement>('#gtd-status');
    if (!statusSel) throw new Error('no status select');
    setValue(statusSel, 'done');
    await clickAsync(button('Save'));

    await clickAsync(undoFor('water the plants'));

    expect(container.textContent).toContain('next occurrence already exists');
  });

  it('deleting a todo inside the window drops its pending undo', async () => {
    const item = todo({ id: 93, title: 'gone for good', status: 'next_action' });
    act(() => { queueUndo(item); });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    await act(async () => {
      root.render(
        <>
          <TodoEditSheet todo={{ ...item, status: 'done' }} projects={[]} contexts={[]}
                         onClose={() => {}} onSaved={() => {}} />
          <UndoPill />
        </>,
      );
    });

    await clickAsync(button('Delete'));

    // Nothing may offer to un-complete a row that no longer exists.
    expect(pill()).toBeNull();
    expect(deleteTodoMock).toHaveBeenCalledWith(93);
    confirm.mockRestore();
  });
});

/** A list page that fetches its own rows — the shape SearchPage and
 *  ProjectDetailPage have. Undo must reach it even though it never saw the
 *  completion, which is the whole point of the broadcast. */
function BroadcastHarness({ onReload }: { onReload: () => void }) {
  useTodosChanged(onReload);
  // TodoShell owns the real toast stack; this stands in for it so the failure
  // path's message is asserted where a user would actually read it.
  const toasts = useToasts();
  return (
    <>
      <UndoPill />
      {toasts.map(t => <p key={t.id}>{t.message}</p>)}
    </>
  );
}

describe('undo side effects', () => {
  it('tells every mounted list to reload, so the todo reappears where it belongs', async () => {
    const reload = vi.fn();
    await act(async () => { root.render(<BroadcastHarness onReload={reload} />); });
    act(() => { queueUndo(todo({ id: 81, title: 'reappear' })); });
    expect(reload).not.toHaveBeenCalled();

    await clickAsync(undoFor('reappear'));

    expect(reload).toHaveBeenCalledTimes(1);
    expect(refreshMetaMock).toHaveBeenCalledTimes(1);
  });

  it('says so and refreshes nothing when the revert is rejected', async () => {
    const reload = vi.fn();
    await act(async () => { root.render(<BroadcastHarness onReload={reload} />); });
    act(() => { queueUndo(todo({ id: 82, title: 'server says no' })); });
    updateTodoMock.mockRejectedValueOnce(new Error('500'));

    await clickAsync(undoFor('server says no'));

    // The row still goes — the todo is recoverable from the Done list — but
    // nothing may claim it came back.
    expect(pill()).toBeNull();
    expect(reload).not.toHaveBeenCalled();
    expect(refreshMetaMock).not.toHaveBeenCalled();
    expect(container.textContent).toContain("it's still marked done");
  });
});

// ── #2925 — the same block, for the OTHER way an item leaves a list ──────────
//
// Setting a context on a triage card is the one write that files an inbox item
// (#1548), and it writes TWO fields at once. Three things here are silent when
// they break:
//
//  1. Undo has to put the CONTEXT back as well as the status. Restoring the
//     status alone leaves the item in the inbox wearing a context the user did
//     not choose — and it passes every fixture whose context started empty,
//     which is most of them. `arrived carrying a context` exists for that.
//  2. A filing spawns nothing, so the "its next occurrence already exists"
//     notice must not fire for one. A repeating inbox item is the only fixture
//     that can catch it.
//  3. The copy on the row and on the button still said "done". Rows read out of
//     context are the only place that shows.

/** Step 3's picker keys its options by INDEX into the card's context list. */
async function pickContext(index: number) {
  const select = container.querySelector<HTMLSelectElement>(
    'select[aria-label="Set context and file this todo"]',
  );
  if (!select) throw new Error('no context picker');
  setValue(select, String(index));
  await act(async () => {});
}

/** TriageCard + the block + the toast stack, which is where the failure message
 *  for a rejected revert is actually read. */
function TriageHarness({ item, contexts }: { item: Todo; contexts: string[] }) {
  const toasts = useToasts();
  return (
    <>
      <TriageCard todo={item} projects={[]} contexts={contexts}
                  onProcessed={() => {}} onChanged={() => {}} onEdit={() => {}} />
      <UndoPill />
      {toasts.map(t => <p key={t.id}>{t.message}</p>)}
    </>
  );
}

describe('#2925 — filing an inbox item under a context', () => {
  const renderTriage = async (item: Todo, contexts: string[]) => {
    await act(async () => { root.render(<TriageHarness item={item} contexts={contexts} />); });
  };

  it('queues a row saying what happened, and undoes the whole filing write', async () => {
    const item = todo({ id: 91, title: 'call the roofer', status: 'inbox', context: '' });
    await renderTriage(item, ['@calls', '@errands']);

    await pickContext(0);

    // The filing itself: context and destination in ONE write (#1548).
    expect(updateTodoMock).toHaveBeenCalledWith(91, { context: '@calls', status: 'next_action' });
    expect(rows()).toHaveLength(1);
    expect(headerCount()).toBe('1 filed from inbox');
    expect(rows()[0].textContent).toContain('Context set');

    await clickAsync(undoFiledFor('call the roofer'));

    expect(updateTodoMock).toHaveBeenLastCalledWith(91, { status: 'inbox', context: '' });
    expect(pill()).toBeNull();
  });

  it('restores the context the item arrived carrying, not an empty one', async () => {
    // Quick Add parses "@errands" out of the captured text and keeps status
    // inbox, so an item can already wear a context before it is ever triaged.
    // Undoing a re-file has to put THAT back — blanking it is a silent edit of
    // something the user never touched.
    const item = todo({ id: 92, title: 'return the drill', status: 'inbox', context: '@errands' });
    // The card puts the item's own context first, then the known ones.
    await renderTriage(item, ['@calls']);

    await pickContext(1);

    expect(updateTodoMock).toHaveBeenCalledWith(92, { context: '@calls', status: 'next_action' });
    await clickAsync(undoFiledFor('return the drill'));
    expect(updateTodoMock).toHaveBeenLastCalledWith(92, { status: 'inbox', context: '@errands' });
  });

  it('never claims a filing spawned a repeating occurrence', async () => {
    // The notice belongs to reopening a COMPLETED repeat: the backend spawns the
    // successor on the transition into done. A filing never completed anything,
    // so saying it here would be a plain lie about the user's data.
    const item = todo({ id: 93, title: 'water bill', status: 'inbox', repeat: 'monthly' });
    await renderTriage(item, ['@home']);

    await pickContext(0);
    await clickAsync(undoFiledFor('water bill'));

    expect(container.textContent).not.toContain('next occurrence already exists');
  });

  it('says it is still filed — not still marked done — when the revert is rejected', async () => {
    const item = todo({ id: 94, title: 'fix the gate', status: 'inbox' });
    await renderTriage(item, ['@home']);
    await pickContext(0);
    updateTodoMock.mockRejectedValueOnce(new Error('500'));

    await clickAsync(undoFiledFor('fix the gate'));

    expect(container.textContent).toContain("it's still filed");
    expect(container.textContent).not.toContain('still marked done');
    expect(refreshMetaMock).not.toHaveBeenCalled();
  });

  it('a later status write drops a pending filing undo', async () => {
    // The same rule Codex found for completions: the offer is only valid while
    // the todo is still where the write put it. Clicking it after the user has
    // moved the item on would re-file it against a decision made later.
    const item = todo({ id: 95, title: 'refiled again', status: 'inbox', context: '@home' });
    await act(async () => { root.render(<SetStatusHarness item={item} />); });
    act(() => { queueUndo(item, 'filed'); });
    expect(rows()).toHaveLength(1);

    await clickAsync(button('to next'));

    expect(pill()).toBeNull();
  });
});

describe('#2925 — one block, two kinds of undo', () => {
  const done = todo({ id: 96, title: 'shred the box', status: 'someday_maybe' });
  const filed = todo({ id: 97, title: 'book the van', status: 'inbox', context: '@errands' });

  it('names the action while the queue is one kind, and stops naming it when it is two', () => {
    renderPill();
    act(() => { queueUndo(done); });
    // #2879's wording, unchanged, for the case it was locked for.
    expect(headerCount()).toBe('1 marked done');

    act(() => { queueUndo(filed, 'filed'); });
    // Two actions, so the header cannot name one — the rows carry it instead.
    expect(headerCount()).toBe('2 recent changes');
    expect(rows()[0].textContent).toContain('Marked done');
    expect(rows()[1].textContent).toContain('Context set');
  });

  it('Undo all puts a completion and a filing each back its own way', async () => {
    renderPill();
    act(() => { queueUndo(done); queueUndo(filed, 'filed'); });

    await clickAsync(button('Undo all'));

    expect(updateTodoMock).toHaveBeenCalledWith(96, { status: 'someday_maybe' });
    expect(updateTodoMock).toHaveBeenCalledWith(97, { status: 'inbox', context: '@errands' });
    expect(pill()).toBeNull();
  });
});

// ── Stage-1 review findings (#2925) ─────────────────────────────────────────

/** The edit sheet + the block + a record of every triage focus the undo asks for. */
function SheetHarness({ item, contexts = [] }: { item: Todo; contexts?: string[] }) {
  const toasts = useToasts();
  return (
    <>
      <TodoEditSheet todo={item} projects={[]} contexts={contexts}
                     onClose={() => {}} onSaved={() => {}} />
      <UndoPill />
      {toasts.map(t => <p key={t.id}>{t.message}</p>)}
    </>
  );
}

const statusSelect = () => {
  const el = container.querySelector<HTMLSelectElement>('#gtd-status');
  if (!el) throw new Error('no status select');
  return el;
};

describe('the edit sheet and a pending filing undo', () => {
  // Correctness found this: the drop guard read `status !== 'done'`, which was an
  // accurate stand-in for "the status moved" only while every queued row sat AT
  // done. A #2925 row sits at a non-done status, so an unrelated save inside the
  // window matched it and silently cancelled an undo still on screen.
  it('survives an unrelated edit that resubmits the same status', async () => {
    const item = todo({ id: 101, title: 'sweep the dock', status: 'next_action', context: '@work' });
    await act(async () => { root.render(<SheetHarness item={item} />); });
    act(() => { queueUndo(item, 'filed'); });
    expect(rows()).toHaveLength(1);

    // Touch something that is not the status at all.
    const title = container.querySelector<HTMLInputElement>('#gtd-title')!;
    setValue(title, 'sweep the dock properly');
    await clickAsync(button('Save'));

    expect(updateTodoMock).toHaveBeenLastCalledWith(101, expect.objectContaining({
      title: 'sweep the dock properly', status: 'next_action',
    }));
    // Still offered, and still offering to put the filing back.
    expect(rows()).toHaveLength(1);
    await clickAsync(undoFiledFor('sweep the dock'));
    expect(updateTodoMock).toHaveBeenLastCalledWith(101, { status: 'next_action', context: '@work' });
  });

  it('still drops the row when the save really does move the status', async () => {
    // The half the guard exists for: undoing after this would overwrite the
    // destination the user has just deliberately chosen.
    const item = todo({ id: 102, title: 'chase the invoice', status: 'next_action', context: '@calls' });
    await act(async () => { root.render(<SheetHarness item={item} />); });
    act(() => { queueUndo(item, 'filed'); });
    expect(rows()).toHaveLength(1);

    setValue(statusSelect(), 'waiting_for');
    await clickAsync(button('Save'));

    expect(pill()).toBeNull();
  });

  it('files an inbox item from the sheet with the same undo the picker gives', async () => {
    // TriageCard's Edit button opens THIS sheet on the item being triaged, so it
    // is the second way out of the inbox and carries the same mis-click.
    const item = todo({ id: 103, title: 'book the dentist', status: 'inbox', context: '' });
    await act(async () => { root.render(<SheetHarness item={item} contexts={['@calls']} />); });

    const ctx = container.querySelector<HTMLSelectElement>('#gtd-context')!;
    setValue(ctx, '0'); // @calls
    setValue(statusSelect(), 'next_action');
    await clickAsync(button('Save'));

    expect(rows()).toHaveLength(1);
    expect(rows()[0].textContent).toContain('Context set');
    await clickAsync(undoFiledFor('book the dentist'));
    expect(updateTodoMock).toHaveBeenLastCalledWith(103, { status: 'inbox', context: '' });
  });
});

/** Records every triage focus the undo path asks for, in order. */
function FocusHarness({ seen }: { seen: number[] }) {
  const toasts = useToasts();
  useInboxFocus(id => { seen.push(id); });
  return (
    <>
      <UndoPill />
      {toasts.map(t => <p key={t.id}>{t.message}</p>)}
    </>
  );
}

describe('Undo all when only some reverts land', () => {
  // Testing found all three of these by mutation: each one passed the whole suite
  // while broken, and each is a claim the app makes about the user's own data.
  const filedA = todo({ id: 111, title: 'first filing', status: 'inbox', context: '@a' });
  const filedB = todo({ id: 112, title: 'second filing', status: 'inbox', context: '@b' });

  it('points the triage card at the filing that actually came back', async () => {
    const seen: number[] = [];
    await act(async () => { root.render(<FocusHarness seen={seen} />); });
    act(() => { queueUndo(filedA, 'filed'); queueUndo(filedB, 'filed'); });
    // The LAST row is the one whose revert fails, so the naive "take the last
    // entry" answer and the correct one differ.
    updateTodoMock
      .mockResolvedValueOnce(BASE)
      .mockRejectedValueOnce(new Error('500'));

    await clickAsync(button('Undo all'));

    expect(seen).toEqual([111]);
  });

  it('reloads nothing when every revert in the batch fails', async () => {
    const seen: number[] = [];
    await act(async () => { root.render(<FocusHarness seen={seen} />); });
    act(() => { queueUndo(filedA, 'filed'); queueUndo(filedB, 'filed'); });
    updateTodoMock.mockRejectedValue(new Error('500'));

    await clickAsync(button('Undo all'));

    // Nothing came back, so nothing may claim it did.
    expect(seen).toEqual([]);
    expect(refreshMetaMock).not.toHaveBeenCalled();
    expect(container.textContent).toContain("it's still filed");
  });

  it('counts only completions when it warns about respawned occurrences', async () => {
    const repeatingFiled = todo({ id: 113, title: 'weekly report', status: 'inbox', repeat: 'weekly' });
    const repeatingDone = todo({ id: 114, title: 'weekly review', status: 'next_action', repeat: 'weekly' });
    await act(async () => { root.render(<FocusHarness seen={[]} />); });
    act(() => { queueUndo(repeatingFiled, 'filed'); queueUndo(repeatingDone); });

    await clickAsync(button('Undo all'));

    // ONE completion was reopened, so the singular notice — never the plural, which
    // is what counting the filing as well would produce.
    expect(container.textContent).toContain('its next occurrence already exists');
    expect(container.textContent).not.toContain('their next occurrences already exist');
  });
});

describe('a later context choice and a pending row (#2925, Codex stage 2)', () => {
  // Which field retires the offer depends on what is pending, and the two cases
  // pull in opposite directions — so both are pinned. A naive "also drop when the
  // context changed" would pass the first and break the second.
  it('retires a FILING row when the sheet re-files under a different context', async () => {
    // The sheet sees the row AFTER the filing; the queued entry was built from the
    // row before it, which is what undo would put back.
    const item = todo({ id: 121, title: 'order the belts', status: 'next_action', context: '@calls' });
    await act(async () => { root.render(<SheetHarness item={item} contexts={['@calls', '@errands']} />); });
    act(() => { queueUndo({ ...item, status: 'inbox', context: '' }, 'filed'); });
    expect(rows()).toHaveLength(1);

    // Status untouched; only the context moves.
    const ctx = container.querySelector<HTMLSelectElement>('#gtd-context')!;
    setValue(ctx, '1'); // @errands
    await clickAsync(button('Save'));

    expect(updateTodoMock).toHaveBeenLastCalledWith(121, expect.objectContaining({
      context: '@errands', status: 'next_action',
    }));
    // Undoing now would overwrite the context the user just chose.
    expect(pill()).toBeNull();
  });

  it('leaves a MARK-DONE row alone when only the context changes', async () => {
    // A completion row puts back the status and nothing else, so a context edit
    // does not move the todo off where marking it done left it.
    const item = todo({ id: 122, title: 'shred the box', status: 'done', context: '@home' });
    await act(async () => { root.render(<SheetHarness item={item} contexts={['@home', '@work']} />); });
    act(() => { queueUndo({ ...item, status: 'next_action' }); });
    expect(rows()).toHaveLength(1);

    const ctx = container.querySelector<HTMLSelectElement>('#gtd-context')!;
    setValue(ctx, '1'); // @work
    await clickAsync(button('Save'));

    expect(rows()).toHaveLength(1);
    await clickAsync(undoFor('shred the box'));
    expect(updateTodoMock).toHaveBeenLastCalledWith(122, { status: 'next_action' });
  });
});

describe('review-stage additions (#231)', () => {
  it('re-focuses the triage card for ANY undo that lands an item back in the inbox', async () => {
    // "Took 2 minutes — Done ✓" completes straight from triage, so its undo restores
    // 'inbox' exactly as a filing's does. Keying the focus on the kind left that item
    // wherever `items[0]` put it; keying it on where the revert lands does not.
    const seen: number[] = [];
    await act(async () => { root.render(<FocusHarness seen={seen} />); });
    act(() => {
      queueUndo(todo({ id: 131, title: 'two-minute job', status: 'inbox' }));
      queueUndo(todo({ id: 132, title: 'off a list', status: 'next_action' }));
    });

    await clickAsync(undoFor('off a list'));
    expect(seen).toEqual([]); // not an inbox item, so there is no card to promote it to

    await clickAsync(undoFor('two-minute job'));
    expect(seen).toEqual([131]);
  });

  it('replaces a pending filing when the same todo is then marked done', () => {
    // One row per todo whatever the kinds: two rows would offer two conflicting reverts
    // for one todo, and whichever the user did not click would re-apply a stale state.
    renderPill();
    const item = todo({ id: 133, title: 'filed then finished', status: 'inbox', context: '' });
    act(() => { queueUndo(item, 'filed'); });
    act(() => { queueUndo({ ...item, status: 'next_action', context: '@calls' }); });

    expect(rows()).toHaveLength(1);
    expect(rows()[0].textContent).toContain('Marked done');
    expect(headerCount()).toBe('1 marked done');
  });
});

describe('rows belong to the session that queued them (#231, Codex connector)', () => {
  // `AuthContext` adopts a token another tab broadcasts, with no reload, so the seat can
  // change under an open block. The previous seat's rows must neither show nor revert —
  // clicking one would re-issue the PUT with the NEW seat's token.
  it('hides and refuses to revert a row queued under another session', async () => {
    sessionStorage.setItem('cakecrm_token', 'seat-a');
    renderPill();
    let key = 0;
    act(() => { key = queueUndo(todo({ id: 141, title: 'seat A did this' })).key; });
    expect(rows()).toHaveLength(1);

    // Another account signs in from a second tab; this tab adopts its token.
    sessionStorage.setItem('cakecrm_token', 'seat-b');
    renderPill();
    expect(pill()).toBeNull();

    await act(async () => { await undoOne(key); });
    expect(updateTodoMock).not.toHaveBeenCalled();
  });

  it('Undo all reverts only the current session\'s rows', async () => {
    sessionStorage.setItem('cakecrm_token', 'seat-a');
    renderPill();
    act(() => { queueUndo(todo({ id: 142, title: 'old seat' })); });
    sessionStorage.setItem('cakecrm_token', 'seat-b');
    act(() => {
      queueUndo(todo({ id: 143, title: 'this seat, one' }));
      queueUndo(todo({ id: 144, title: 'this seat, two' }));
    });

    await clickAsync(button('Undo all'));

    expect(updateTodoMock).toHaveBeenCalledTimes(2);
    expect(updateTodoMock).not.toHaveBeenCalledWith(142, expect.anything());
  });
});
