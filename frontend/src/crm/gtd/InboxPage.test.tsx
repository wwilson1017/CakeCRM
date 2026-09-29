// @vitest-environment jsdom
//
// #231 (port of cake_os #2925) — the half of undoing a filing that lives on the Inbox
// page rather than in the pill: the restored item has to come back as the CURRENT triage
// card, with a card that still works. Triage is not a list — the item does not simply
// reappear in a row the user is already looking at, it goes back into a queue whose head
// the page picks — so every case below passes with the revert wired up and the page left
// alone, which is exactly why they exist.
//
// createRoot + React act, following the repo's other component tests — no RTL.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { listTodosMock, updateTodoMock } = vi.hoisted(() => ({
  listTodosMock: vi.fn(),
  updateTodoMock: vi.fn(),
}));
vi.mock('./api', () => ({
  listTodos: listTodosMock,
  updateTodo: updateTodoMock,
  todayTodos: vi.fn().mockResolvedValue([]),
  createTodo: vi.fn(),
  deleteTodo: vi.fn(),
  listProjects: vi.fn().mockResolvedValue([]),
  createProject: vi.fn(),
  updateProject: vi.fn(),
  deleteProject: vi.fn(),
  getFilters: vi.fn().mockResolvedValue({ contexts: ['@calls'], tags: [], status_counts: {} }),
  getCaptureLink: vi.fn(),
  regenerateCaptureLink: vi.fn(),
}));

import { InboxPage } from './InboxPage';
import type { Todo } from './types';
import { notifyTodosChanged, resetInboxFocus } from './hooks';
import { resetUndoQueue } from './undoQueue';

const base: Omit<Todo, 'id' | 'title'> = {
  notes: '', project_id: null, project_name: null, context: '', tags: [],
  status: 'inbox', star: false, due_date: '', repeat: '',
  auto_star_on_due: false, source: 'web',
  created_at: '2026-08-06T12:00:00Z', updated_at: '2026-08-06T12:00:00Z', completed_at: null,
  contact_id: null, deal_id: null,
};
const FIRST: Todo = { ...base, id: 1, title: 'first captured' };
const SECOND: Todo = { ...base, id: 2, title: 'second captured' };

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  listTodosMock.mockReset().mockResolvedValue([FIRST, SECOND]);
  updateTodoMock.mockReset().mockResolvedValue(FIRST);
  resetUndoQueue();
  resetInboxFocus();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  resetUndoQueue();
});

async function render() {
  await act(async () => {
    root.render(<MemoryRouter><InboxPage /></MemoryRouter>);
  });
}

/** The item currently on the big triage card. Its click-to-rename title is the one
 *  rename affordance on the page — the queue rows below are promote-only. */
const onCard = () =>
  container.querySelector('[role="button"][title="Click to rename"]')?.textContent ?? null;

/** The compact "N more in inbox" rows below the card.
 *
 * Keyed on the list's `data-inbox-queue` marker, not on a Tailwind class: this
 * used to select `button.truncate` and went silently empty the moment those
 * titles were changed to wrap. */
const restRows = () =>
  [...container.querySelectorAll<HTMLButtonElement>('[data-inbox-queue] button')];

const click = (el: Element) => act(() => { (el as HTMLElement).click(); });

/** The card's context picker — the affordance that has to still work afterwards. */
const picker = () =>
  container.querySelector<HTMLSelectElement>('select[aria-label="Set context and file this todo"]')!;

/** File whatever is on the card under the first offered context. */
async function fileFromCard() {
  const el = picker();
  const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')?.set;
  await act(async () => {
    setter?.call(el, '0');
    el.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

async function clickUndoFiling(title: string) {
  const el = container.querySelector<HTMLButtonElement>(`button[aria-label="Undo filing “${title}”"]`);
  if (!el) throw new Error(`no filing-undo button for "${title}"`);
  await act(async () => { el.click(); });
}

describe('InboxPage — undoing a filing (#231)', () => {
  it('promotes the restored item to the card instead of leaving it down the queue', async () => {
    await render();
    click(restRows()[0]); // triage "second captured", which is NOT the head
    expect(onCard()).toBe('second captured');

    listTodosMock.mockResolvedValue([FIRST]); // filed, so it leaves the inbox
    await fileFromCard();
    expect(updateTodoMock).toHaveBeenCalledWith(2, { context: '@calls', status: 'next_action' });
    expect(onCard()).toBe('first captured');

    listTodosMock.mockResolvedValue([FIRST, SECOND]); // and it comes back
    await clickUndoFiling('second captured');

    // The point of the issue. `items[0]` is "first captured"; without the page
    // taking the focus the undo would be invisible and the user would have no
    // way to see it took effect.
    expect(updateTodoMock).toHaveBeenLastCalledWith(2, { status: 'inbox', context: '' });
    expect(onCard()).toBe('second captured');
  });

  it('gives the restored item a working card even when the undo outran the refetch', async () => {
    // `TriageCard` stays `busy` after a resolving write on purpose — it is spent,
    // and it counts on the reload swapping in a different head item to remount
    // it. But `useTodos` cancels a superseded fetch, so an undo clicked inside
    // that window cancels the filing's refetch: the list never drops the item,
    // and the same spent card would still be mounted under the same id with its
    // picker disabled — an item back in the inbox that cannot be triaged again.
    await render();
    expect(onCard()).toBe('first captured'); // file the HEAD, so the id does not change

    await fileFromCard(); // listTodos still answers [FIRST, SECOND]
    expect(updateTodoMock).toHaveBeenCalledWith(1, { context: '@calls', status: 'next_action' });
    expect(picker().disabled).toBe(true); // spent, as designed

    await clickUndoFiling('first captured');

    expect(onCard()).toBe('first captured');
    expect(picker().disabled).toBe(false);
  });

  it('clears a filter the restored item would otherwise be hidden behind', async () => {
    // Reachable because `TodoEditSheet` files an inbox item too, and THAT write
    // carries the title as well — so an item renamed while being filed comes back
    // wearing a title the active search no longer matches. Selecting it would then
    // fall straight through to `items[0]` and the undo would look like a no-op.
    await render();
    const search = container.querySelector<HTMLInputElement>('input[placeholder="Search inbox…"]')!;
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(search), 'value')?.set;
    await act(async () => {
      setter?.call(search, 'first');
      search.dispatchEvent(new Event('input', { bubbles: true }));
    });
    // `SearchInput` settles the query through a 250ms debounce before the page
    // ever sees it, so asserting on the unsettled box would pass against no filter
    // at all — which is what the first draft of this test did.
    await act(async () => { await new Promise(r => setTimeout(r, 300)); });
    // The filter is real: only FIRST is on the card, SECOND is filtered away.
    expect(onCard()).toBe('first captured');
    expect(restRows()).toHaveLength(0);

    act(() => { notifyTodosChanged(2); }); // an undo restores "second captured"

    expect(onCard()).toBe('second captured');
    expect(search.value).toBe('');
  });

  it('takes a focus raised while the Inbox was not even mounted', async () => {
    // The pill outlives the shell — `TodoShell` unmounts on every tab switch, so
    // a filing undone from Today has no InboxPage listening at all. The request
    // has to wait for the page rather than be broadcast into nothing, or walking
    // back to the Inbox lands on whatever `items[0]` is and the correction the
    // user asked for is invisible. `notifyTodosChanged` is the seam the undo path
    // reaches the pages through; this drives it directly because the whole point
    // is that no page is mounted at the time.
    act(() => { notifyTodosChanged(2); });

    await render();

    expect(onCard()).toBe('second captured');
  });
});
