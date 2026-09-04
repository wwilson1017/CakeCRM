// @vitest-environment jsdom
//
// Covers the four triage defects this port fixes, and only those — the card's existing
// three-step flow is exercised by the app's own surfaces and was not touched here.
//
// Every property below is invisible to tsc and eslint, which is the whole reason the file
// exists: that a native date input ignores `placeholder` (so the empty-state cue has to be
// an overlay, and has to get out of the way when the field is in use), that the notes box
// commits on blur WITHOUT taking the card-wide busy flag, that a note is on the row before
// the item leaves the inbox, and that a note the server rejects still cannot trap the item
// there.
//
// createRoot + React act, following the repo's other component tests — no RTL.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

const { updateTodoMock, deleteTodoMock, createProjectMock } = vi.hoisted(() => ({
  updateTodoMock: vi.fn(),
  deleteTodoMock: vi.fn(),
  createProjectMock: vi.fn(),
}));
vi.mock('../api', () => ({
  updateTodo: updateTodoMock,
  deleteTodo: deleteTodoMock,
  createProject: createProjectMock,
}));

import { TriageCard } from './TriageCard';
import { toast } from '../../../shared/toast';
import type { Todo, TodoProject } from '../types';

const TODO: Todo = {
  id: 7,
  title: 'dentist',
  notes: '',
  project_id: null,
  project_name: null,
  context: '',
  tags: [],
  status: 'inbox',
  star: false,
  due_date: '',
  repeat: '',
  auto_star_on_due: false,
  source: 'web',
  created_at: '2026-08-06T12:00:00Z',
  updated_at: '2026-08-06T12:00:00Z',
  completed_at: null,
  contact_id: null,
  deal_id: null,
};

const PROJECT: TodoProject = {
  id: 3, name: 'Kitchen remodel', notes: '', status: 'active', open_count: 2,
  created_at: '2026-08-06T12:00:00Z', updated_at: '2026-08-06T12:00:00Z',
};

let container: HTMLDivElement;
let root: Root;
let onProcessed: Mock<() => void>;
let onChanged: Mock<() => void>;
let onEdit: Mock<(t: Todo) => void>;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  updateTodoMock.mockReset().mockImplementation((_id, fields) =>
    Promise.resolve({ ...TODO, ...fields }));
  deleteTodoMock.mockReset().mockResolvedValue(undefined);
  createProjectMock.mockReset().mockResolvedValue({ ...PROJECT, id: 9, name: 'Garage' });
  onProcessed = vi.fn();
  onChanged = vi.fn();
  onEdit = vi.fn();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks(); // the toast spies are per-test — don't leak them
});

function render(todo: Partial<Todo> = {}, contexts = ['@calls', '@errands'], projects = [PROJECT]) {
  act(() => {
    root.render(
      <TriageCard
        todo={{ ...TODO, ...todo }}
        projects={projects}
        contexts={contexts}
        onProcessed={onProcessed}
        onChanged={onChanged}
        onEdit={onEdit}
      />,
    );
  });
}

/** React tracks the value it last rendered, so a plain `el.value = x` is invisible to
 * it — go through the prototype setter the way React's own tracker does. */
function setValue(el: HTMLSelectElement | HTMLTextAreaElement | HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')?.set;
  act(() => {
    setter?.call(el, value);
    el.dispatchEvent(
      new Event(el instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }),
    );
  });
}

const click = (el: Element) => { act(() => { (el as HTMLElement).click(); }); };
const settle = () => act(async () => {});

// React delegates focus at the root, so a non-bubbling `blur`/`focus` never reaches it.
const focus = (el: Element) => {
  act(() => { el.dispatchEvent(new FocusEvent('focusin', { bubbles: true })); });
};
const unfocus = (el: Element) => {
  act(() => { el.dispatchEvent(new FocusEvent('focusout', { bubbles: true })); });
};

function button(label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll('button')].find(
    b => (b.textContent ?? '').trim() === label,
  );
  if (!found) throw new Error(`no button labelled "${label}"`);
  return found;
}

const dueInput = () => container.querySelector<HTMLInputElement>('input[aria-label="Due date"]')!;
const notesBox = () => container.querySelector<HTMLTextAreaElement>('textarea[aria-label="Notes"]')!;
const contextPicker = () =>
  container.querySelector<HTMLSelectElement>('select[aria-label="Set context and file this todo"]')!;
const cue = () =>
  [...container.querySelectorAll('span[aria-hidden="true"]')]
    .find(el => (el.textContent ?? '').trim() === 'Add due date');

describe('the step headings are legible', () => {
  // A class assertion, deliberately: the headings were `text-xs`/`text-muted`, which is a
  // rendering property no type or lint rule can see, and the palette guard in
  // `core/theme/inkContrast.test.ts` measures the TOKENS, not which token a heading picks.
  // `text-charcoal` aliases `--color-ck-ink`, the primary body ink that guard already pins
  // at AA on `ck-card` in both themes — so naming it here is what ties this surface to it.
  it('use the primary ink at body size, not muted fine print', () => {
    render();
    const headings = [...container.querySelectorAll('h3')];
    expect(headings.map(h => (h.textContent ?? '').trim())).toEqual([
      '1. What kind of action?',
      '2. Add detail (optional)',
      '3. Last step — set context (required)',
    ]);
    for (const h of headings) {
      expect(h.className).toContain('text-sm');
      expect(h.className).toContain('text-charcoal');
      expect(h.className).not.toContain('text-muted');
    }
  });
});

describe('step 2 — the due-date cue', () => {
  // A native <input type="date"> ignores `placeholder` and renders the browser's own
  // mm/dd/yyyy instead, so "Add due date" is an overlay. Nothing tsc or eslint sees can
  // tell whether that overlay is present, gone once a date exists, or — the one that
  // actually breaks the control — still sitting on top of the native editor mid-pick.

  it('labels the empty box in place of the missing placeholder', () => {
    render();
    expect(cue()).toBeTruthy();
  });

  it('goes away once the todo has a due date', () => {
    // Asserting the absence alone would also pass on a card that has no cue at all, so
    // establish it first — the same reason the focus case below re-renders empty.
    render();
    expect(cue()).toBeTruthy();
    render({ due_date: '2026-09-10' });
    expect(cue()).toBeUndefined();
  });

  it('uncovers the native editor while the field has focus', () => {
    render();
    expect(cue()).toBeTruthy();
    focus(dueInput());
    expect(cue()).toBeUndefined();
  });

  it('comes back after the date is cleared', async () => {
    render({ due_date: '2026-09-10' });
    setValue(dueInput(), '');
    await settle();
    expect(updateTodoMock).toHaveBeenCalledWith(7, { due_date: '' });
    // The write disables the input, so React never calls onBlur — the cue would stay
    // suppressed by a focus that ended long ago if onChange did not clear the flag too.
    expect(cue()).toBeTruthy();
  });

  it('is inert to the pointer, so tapping it still opens the picker', () => {
    render();
    expect(cue()!.className).toContain('pointer-events-none');
  });

  it('keeps the picked date on screen until the reload lands', async () => {
    // The input is CONTROLLED by the prop, which the parent refetches asynchronously.
    // Without the optimistic value the field reverts the instant the write is sent and the
    // cue flashes back over the date just chosen.
    render();
    setValue(dueInput(), '2026-09-10');
    await settle();
    expect(dueInput().value).toBe('2026-09-10');
    expect(cue()).toBeUndefined();
  });

  it('drops the optimistic date when the write fails', async () => {
    updateTodoMock.mockRejectedValueOnce(new Error('offline'));
    render();
    setValue(dueInput(), '2026-09-10');
    await settle();
    // Nothing was written, so the field must not go on showing a date the server never took.
    expect(dueInput().value).toBe('');
    expect(cue()).toBeTruthy();
  });

  it('hands the Edit sheet the date on screen, not the stale prop', async () => {
    render();
    setValue(dueInput(), '2026-09-10');
    await settle();
    click(button('Edit'));
    await settle();
    expect(onEdit).toHaveBeenCalledTimes(1);
    expect(onEdit.mock.calls[0][0].due_date).toBe('2026-09-10');
  });
});

describe('step 2 — notes without leaving triage', () => {
  it('seeds from the todo and saves on blur without filing the item', async () => {
    render({ notes: 'old' });
    expect(notesBox().value).toBe('old');
    setValue(notesBox(), 'ring 615-555-0100 first');
    unfocus(notesBox());
    await settle();

    expect(updateTodoMock).toHaveBeenCalledWith(7, { notes: 'ring 615-555-0100 first' });
    expect(onChanged).toHaveBeenCalledTimes(1);
    expect(onProcessed).not.toHaveBeenCalled();
  });

  it('replaced the read-only preview rather than rendering both', () => {
    // The old card printed notes under the title. Leaving that in place beside an editable
    // box would show the same text twice, and the two would disagree while a draft is dirty.
    render({ notes: 'call after 4pm' });
    expect(notesBox().value).toBe('call after 4pm');
    const printed = [...container.querySelectorAll('p')].filter(
      p => (p.textContent ?? '').includes('call after 4pm'),
    );
    expect(printed).toHaveLength(0);
  });

  it('writes nothing when the notes were never touched', async () => {
    render({ notes: 'unchanged' });
    unfocus(notesBox());
    await settle();

    expect(updateTodoMock).not.toHaveBeenCalled();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it('writes nothing when the notes are typed back to what they were', async () => {
    // The blur handler cannot just ask "was anything typed?" — an edit and its undo leave a
    // draft equal to the saved text, and writing it costs a round trip and a full parent
    // reload for nothing.
    render({ notes: 'as filed' });
    setValue(notesBox(), 'as filed and then some');
    setValue(notesBox(), 'as filed');
    unfocus(notesBox());
    await settle();

    expect(updateTodoMock).not.toHaveBeenCalled();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it('does not take the card-wide busy flag — the blur must not swallow the next click', async () => {
    // Clicking a button is what blurs this textarea. If the notes write went through
    // patch()'s `busy`, the very click that caused the blur would be dropped and the user
    // would have to press the button twice.
    render();
    setValue(notesBox(), 'two minute job');
    unfocus(notesBox());
    click(button('Took 2 minutes — Done ✓'));
    await settle();

    expect(updateTodoMock).toHaveBeenCalledWith(7, { notes: 'two minute job' });
    expect(updateTodoMock).toHaveBeenCalledWith(7, { status: 'done' });
    expect(onProcessed).toHaveBeenCalledTimes(1);
  });

  it('keeps the typed notes, and hands them to the Edit sheet, when the save fails', async () => {
    // A paragraph of notes is the work, not a keystroke — unlike a failed title save, which
    // reverts, this one must leave the text where the user can still rescue it.
    updateTodoMock.mockRejectedValueOnce(new Error('offline'));
    render();
    setValue(notesBox(), 'the long version');
    unfocus(notesBox());
    await settle();

    expect(notesBox().value).toBe('the long version');
    click(button('Edit'));
    await settle();
    expect(onEdit.mock.calls[0][0].notes).toBe('the long version');
  });

  it('toasts a failed save, because this card can unmount before the message is read', async () => {
    const spy = vi.spyOn(toast, 'error').mockImplementation(() => {});
    updateTodoMock.mockRejectedValueOnce(new Error('offline'));
    render();
    setValue(notesBox(), 'rescue me');
    unfocus(notesBox());
    await settle();

    expect(spy).toHaveBeenCalledWith('offline');
  });

  it('adopts notes the Edit sheet wrote — a same-id reload does not remount this card', () => {
    // `InboxPage` keys the card by todo id, so the sheet saving notes reloads the SAME card.
    // Seeding the box once at mount would leave it showing the pre-sheet text, which the
    // next blur would then write straight back over the edit.
    render({ notes: 'before' });
    render({ notes: 'what the sheet wrote' });
    expect(notesBox().value).toBe('what the sheet wrote');
  });

  it('never discards unsaved notes when a reload lands', () => {
    render({ notes: 'before' });
    setValue(notesBox(), 'still typing this');
    render({ notes: 'an outside edit' });
    expect(notesBox().value).toBe('still typing this');
  });

  it('follows the server again once the write lands', async () => {
    render({ notes: 'before' });
    setValue(notesBox(), 'mine');
    unfocus(notesBox());
    await settle();
    // The baseline moved on acknowledgement, so the box is clean again and takes the next
    // outside change instead of reading dirty for the length of the refetch.
    render({ notes: 'later, from elsewhere' });
    expect(notesBox().value).toBe('later, from elsewhere');
  });
});

describe('a note is part of the triage decision', () => {
  it('is on the row before the item leaves the inbox', async () => {
    // The picker takes focus off the textarea, so in the browser the blur commits first.
    // Firing the pick alone proves the filing path flushes on its own rather than relying
    // on that ordering.
    render();
    setValue(notesBox(), 'gate code 4412');
    setValue(contextPicker(), '0'); // '@calls'
    await settle();

    expect(updateTodoMock).toHaveBeenNthCalledWith(1, 7, { notes: 'gate code 4412' });
    expect(updateTodoMock).toHaveBeenNthCalledWith(2, 7, { context: '@calls', status: 'next_action' });
    expect(onProcessed).toHaveBeenCalledTimes(1);
  });

  it('commits the note exactly once when the blur already sent it', async () => {
    render();
    setValue(notesBox(), 'gate code 4412');
    unfocus(notesBox());
    setValue(contextPicker(), '0');
    await settle();

    const noteWrites = updateTodoMock.mock.calls.filter(c => 'notes' in (c[1] as object));
    expect(noteWrites).toHaveLength(1);
  });

  it('does not trap the item in the inbox when the note is rejected', async () => {
    // Filing is this card's ONE exit. A note the server keeps refusing — 20k characters,
    // say — must not become a locked door; the failure is already said inline and as a toast.
    vi.spyOn(toast, 'error').mockImplementation(() => {});
    updateTodoMock.mockRejectedValueOnce(new Error('notes too long'));
    render();
    setValue(notesBox(), 'x'.repeat(50));
    setValue(contextPicker(), '0');
    await settle();

    expect(updateTodoMock).toHaveBeenNthCalledWith(2, 7, { context: '@calls', status: 'next_action' });
    expect(onProcessed).toHaveBeenCalledTimes(1);
  });

  it('queues a second commit behind the first instead of racing it', async () => {
    // Two writes to the same column, in flight together, land in whichever order the server
    // picks — so the later blur waits, then sends the text as it stands.
    let release: (v: Todo) => void = () => {};
    updateTodoMock.mockImplementationOnce(
      () => new Promise<Todo>(res => { release = res; }),
    );
    render();
    setValue(notesBox(), 'first');
    unfocus(notesBox());
    setValue(notesBox(), 'second');
    unfocus(notesBox());
    await settle();

    // Still only the first request — the second is queued, not racing.
    expect(updateTodoMock).toHaveBeenCalledTimes(1);
    await act(async () => { release({ ...TODO, notes: 'first' }); });
    await settle();

    expect(updateTodoMock).toHaveBeenCalledTimes(2);
    expect(updateTodoMock).toHaveBeenNthCalledWith(2, 7, { notes: 'second' });
  });
});
