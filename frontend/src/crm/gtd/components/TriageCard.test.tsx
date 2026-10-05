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
import { act, StrictMode } from 'react';
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
  id: 3, name: 'Kitchen remodel', notes: '', purpose: '', outcome: '', status: 'active', open_count: 2,
  created_at: '2026-08-06T12:00:00Z', updated_at: '2026-08-06T12:00:00Z',
};

// The card advances its view of the row from whichever source is NEWER, ordered on
// `updated_at`. So the fixtures have to move that column the way the server does — with one
// frozen timestamp every response and every reload looks stale, the card ignores them all,
// and the suite would be testing a component that never adopts anything.
// …and in the shape the server emits: `_apply_todo_update_cur` stamps `updated_at` from
// `datetime.now(timezone.utc).isoformat()`, which is microseconds and a `+00:00` zone — not
// `toISOString()`'s millisecond `Z`. Fixtures in the wrong shape would let a comparison bug
// that only bites production pass here.
let clock = 0;
const nextStamp = () =>
  `2026-08-06T12:00:${String(++clock).padStart(2, '0')}.000000+00:00`;

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
  clock = 0;
  updateTodoMock.mockReset().mockImplementation((_id, fields) =>
    Promise.resolve({ ...TODO, ...fields, updated_at: nextStamp() }));
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
  // Each render is a fresh row unless the case pins its own `updated_at`, so a re-render
  // stands for the parent's refetch landing rather than for nothing at all.
  const next: Todo = { ...TODO, updated_at: nextStamp(), ...todo };
  act(() => {
    root.render(
      <TriageCard
        todo={next}
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
const titleText = () =>
  container.querySelector<HTMLElement>('span[role="button"]')!.textContent;
const projectPicker = () =>
  container.querySelector<HTMLSelectElement>('select[aria-label="Project"]')!;
const newProjectInput = () =>
  container.querySelector<HTMLInputElement>('input[aria-label="New project name"]')!;
const submitForm = (el: Element) => {
  act(() => {
    el.closest('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
};
const startTitleSave = (to: string) => {
  click(container.querySelector('span[role="button"]')!);
  const editor = container.querySelector<HTMLInputElement>('input[aria-label="Todo title"]')!;
  setValue(editor, to);
  unfocus(editor);
};
const cue = () =>
  [...container.querySelectorAll('span[aria-hidden="true"]')]
    .find(el => (el.textContent ?? '').trim() === 'Add due date');
/** What an assistive technology announces: everything the element renders, visually-hidden
 * text included, minus every `aria-hidden` subtree. */
const accessibleText = (el: Element) => {
  const clone = el.cloneNode(true) as Element;
  for (const hidden of clone.querySelectorAll('[aria-hidden="true"]')) hidden.remove();
  return (clone.textContent ?? '').replace(/\s+/g, ' ').trim();
};
/** What the eye sees: the same text minus every visually-hidden span. */
const visibleText = (el: Element) => {
  const clone = el.cloneNode(true) as Element;
  for (const hidden of clone.querySelectorAll('.sr-only')) hidden.remove();
  return (clone.textContent ?? '').replace(/\s+/g, ' ').trim();
};

describe('the step headings are legible', () => {
  // A class assertion, deliberately: the headings were `text-xs`/`text-muted`, which is a
  // rendering property no type or lint rule can see, and the palette guard in
  // `core/theme/inkContrast.test.ts` measures the TOKENS, not which token a heading picks.
  // `text-charcoal` aliases `--color-ck-ink`, the primary body ink that guard already pins
  // at AA on `ck-card` in both themes — so naming it here is what ties this surface to it.
  it('use the primary ink at body size, not muted fine print', () => {
    render();
    const headings = [...container.querySelectorAll('h3')];
    expect(headings).toHaveLength(3);
    for (const h of headings) {
      expect(h.className).toContain('text-sm');
      expect(h.className).toContain('text-charcoal');
      expect(h.className).not.toContain('text-muted');
    }
  });

  // The step number and the "required" marker are glyph-only on screen now, so the thing
  // worth pinning is the ACCESSIBLE name: it must still read as the words the badge and the
  // star replaced. Asserting raw `textContent` would pass on markup that announces a heading
  // as "01", or that drops "required" out of the tree altogether.
  it('keep the step number and the required marker in the accessible name', () => {
    render();
    expect([...container.querySelectorAll('h3')].map(accessibleText)).toEqual([
      'Step 1: What kind of action?',
      'Step 2: Add detail (optional)',
      'Step 3: Last step — set context (required)',
    ]);
  });

  it('carry the number in a solid accent badge beside the heading, not in its text', () => {
    render();
    const headings = [...container.querySelectorAll('h3')];
    const badges = headings.map(h => h.previousElementSibling!);
    expect(badges.map(b => (b.textContent ?? '').trim())).toEqual(['01', '02', '03']);
    for (const b of badges) {
      // The heading's own visually-hidden "Step N" is what announces the number, so the
      // glyph must stay out of the tree rather than say it twice.
      expect(b.getAttribute('aria-hidden')).toBe('true');
      // The one solid-accent pairing `core/theme/hueContrast.test.ts` pins at AA.
      expect(b.className).toContain('bg-ck-accent');
      expect(b.className).toContain('text-ck-accent-ink');
    }
  });

  // The exact counterpart to the accessible-name test: what is PAINTED is only the words,
  // with the number left to the badge and the "required" marker to the star. It is pinned
  // as an equality rather than as negatives, because a negative is the weaker claim by a
  // wide margin — "does not start with a digit" passes happily on a heading rendering
  // "Step 1: What kind of action?", which is what a broken `sr-only` produces.
  it('paint only the words, leaving the number to the badge and the marker to the star', () => {
    render();
    expect([...container.querySelectorAll('h3')].map(visibleText)).toEqual([
      'What kind of action?',
      'Add detail (optional)',
      'Last step — set context★',
    ]);
  });

  it('mark step 3 required with a star, and only step 3', () => {
    render();
    const headings = [...container.querySelectorAll('h3')];
    const stars = headings.map(h => [...h.querySelectorAll('span[aria-hidden="true"]')]
      .find(s => (s.textContent ?? '').trim() === '★'));
    expect(stars.map(Boolean)).toEqual([false, false, true]);
    // The star is a glyph, so it routes through the accent TEXT token, never the fill.
    expect(stars[2]!.className).toContain('text-ck-accent-text');
    // The word is gone from what is painted, but not from what is announced.
    expect(visibleText(headings[2])).not.toContain('required');
    expect(accessibleText(headings[2])).toContain('(required)');
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
    // The focus is what makes this test able to fail. Picking a date sets `busy`, which
    // DISABLES the input — and React does not dispatch to a disabled target, so onBlur
    // never runs. Unless onChange clears the flag itself, `dueFocused` stays stuck true
    // from this focus and goes on suppressing the cue long after the field was left.
    render();
    focus(dueInput());
    expect(cue()).toBeUndefined();

    setValue(dueInput(), '2026-09-10');
    unfocus(dueInput());
    await settle();
    setValue(dueInput(), '');
    unfocus(dueInput());
    await settle();

    expect(updateTodoMock).toHaveBeenLastCalledWith(7, { due_date: '' });
    expect(cue()).toBeTruthy();
  });

  it('stops pinning the date once the row moves on', async () => {
    // The optimistic value shadows the prop, so it has to be RELEASED when the row
    // actually changes — otherwise the field pins the date this card last picked and
    // ignores every later edit, the Edit sheet's own included, for the life of the card.
    render();
    setValue(dueInput(), '2026-09-10');
    unfocus(dueInput());
    await settle();
    expect(dueInput().value).toBe('2026-09-10');

    render({ due_date: '2026-10-01' }); // the sheet moved it, and the parent reloaded
    expect(dueInput().value).toBe('2026-10-01');
  });

  it('survives a date typed a keystroke at a time into an empty box', async () => {
    // The exact sequence a date input emits: it reports a COMPLETE value as soon as every
    // segment parses, so typing "12242026" arrives as four values, one per year digit.
    // Writing the first sets `busy`, which disables the input, and the remaining keystrokes
    // go nowhere — `0002-12-24` is what reaches the server. Measured on the real app.
    render();
    setValue(dueInput(), '0002-12-24');
    setValue(dueInput(), '0020-12-24');
    setValue(dueInput(), '0202-12-24');
    setValue(dueInput(), '2026-12-24');
    unfocus(dueInput());
    await settle();

    expect(updateTodoMock.mock.calls).toEqual([[7, { due_date: '2026-12-24' }]]);
  });

  it('survives a date typed over one the todo already had', async () => {
    // The commoner gesture, and the one a year-shaped guard does not cover: with a date
    // already set, the FIRST keystroke completes a parseable value (`2026-01-01` after the
    // month's "1"), so the truncation lands on the very first key and stores a wrong date
    // rather than none.
    render({ due_date: '2026-10-01' });
    setValue(dueInput(), '2026-01-01');
    setValue(dueInput(), '2026-12-01');
    setValue(dueInput(), '2026-12-02');
    setValue(dueInput(), '2026-12-24');
    unfocus(dueInput());
    await settle();

    expect(updateTodoMock.mock.calls).toEqual([[7, { due_date: '2026-12-24' }]]);
  });

  it('writes what the field is showing, whatever that is', async () => {
    // Blur-committing means the value written is the value on screen. A year left half
    // typed is saved as it reads — wrong, but visibly wrong, which is the whole difference
    // from silently truncating one the user finished typing.
    render();
    setValue(dueInput(), '0020-12-24');
    unfocus(dueInput());
    await settle();

    expect(dueInput().value).toBe('0020-12-24');
    expect(updateTodoMock).toHaveBeenCalledWith(7, { due_date: '0020-12-24' });
  });

  it('writes a date clear', async () => {
    render({ due_date: '2026-09-10' });
    setValue(dueInput(), '');
    unfocus(dueInput());
    await settle();
    expect(updateTodoMock).toHaveBeenCalledWith(7, { due_date: '' });
  });

  it('carries a date just picked into the write that files the item', async () => {
    // The click that files the item is what blurs the field, so the commit and the filing
    // write are the same gesture — and a resolving write flushes the date first.
    render();
    setValue(dueInput(), '2026-12-24');
    setValue(contextPicker(), '0'); // '@calls'
    await settle();

    expect(updateTodoMock).toHaveBeenNthCalledWith(1, 7, { due_date: '2026-12-24' });
    expect(updateTodoMock).toHaveBeenNthCalledWith(2, 7, { context: '@calls', status: 'next_action' });
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
    vi.spyOn(toast, 'error').mockImplementation(() => {});
    updateTodoMock.mockRejectedValueOnce(new Error('offline'));
    render();
    setValue(dueInput(), '2026-09-10');
    unfocus(dueInput());
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

describe('the bring-back date (#261)', () => {
  const backInput = () =>
    container.querySelector<HTMLInputElement>('input[aria-label="Bring back on"]')!;

  it('commits on blur, not on every keystroke', async () => {
    render();
    setValue(backInput(), '2026-11-02');
    expect(updateTodoMock).not.toHaveBeenCalled();
    unfocus(backInput());
    await settle();
    expect(updateTodoMock).toHaveBeenCalledWith(7, { bring_back_on: '2026-11-02' });
    expect(onChanged).toHaveBeenCalled();
    expect(onProcessed).not.toHaveBeenCalled();
  });

  it('clears to null, the column\'s "no date"', async () => {
    render({ bring_back_on: '2026-11-02' });
    expect(backInput().value).toBe('2026-11-02');
    setValue(backInput(), '');
    unfocus(backInput());
    await settle();
    expect(updateTodoMock).toHaveBeenCalledWith(7, { bring_back_on: null });
  });

  it('is carried into the write that files the item', async () => {
    render();
    setValue(backInput(), '2026-11-02');
    setValue(contextPicker(), '0'); // '@calls'
    await settle();
    expect(updateTodoMock).toHaveBeenNthCalledWith(1, 7, { bring_back_on: '2026-11-02' });
    expect(updateTodoMock).toHaveBeenNthCalledWith(2, 7, { context: '@calls', status: 'next_action' });
  });

  it('hands the Edit sheet the date on screen, so its full save cannot revert it', async () => {
    render();
    setValue(backInput(), '2026-11-02');
    await settle();
    click(button('Edit'));
    await settle();
    expect(onEdit.mock.calls[0][0].bring_back_on).toBe('2026-11-02');
  });
});

describe('the inline title is optimistic too', () => {
  // `pendingTitle` predates this port and had the same defect the date override was fixed
  // for: set on a successful rename and never released, so the card would pin the name it
  // last wrote and ignore every later change to it. One adoption block covers both.
  it('stops pinning the title once the row moves on', async () => {
    render();
    click(container.querySelector('span[role="button"]')!);
    const editor = container.querySelector<HTMLInputElement>('input[aria-label="Todo title"]')!;
    setValue(editor, 'dentist — reschedule');
    unfocus(editor);
    await settle();
    expect(updateTodoMock).toHaveBeenCalledWith(7, { title: 'dentist — reschedule' });
    expect(titleText()).toBe('dentist — reschedule');

    render({ title: 'renamed somewhere else' }); // the sheet moved it, and the parent reloaded
    expect(titleText()).toBe('renamed somewhere else');
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

  it('opens the Edit sheet even when its own flush keeps failing', async () => {
    // A draft the server rejects is exactly what the sheet is there to rescue, so the
    // click must not be gated on the write. A persistent rejection, not a one-shot: the
    // blur that precedes the click would otherwise consume it and the flush would succeed.
    vi.spyOn(toast, 'error').mockImplementation(() => {});
    updateTodoMock.mockRejectedValue(new Error('offline'));
    render();
    setValue(notesBox(), 'the long version');
    unfocus(notesBox());
    await settle();
    click(button('Edit'));
    await settle();

    expect(onEdit).toHaveBeenCalledTimes(1);
    expect(onEdit.mock.calls[0][0].notes).toBe('the long version');
  });

  it('opens the sheet on the notes as they stand when the flush finishes', async () => {
    // Opening waits on the flush, which is a round trip, and the textarea stays editable
    // throughout. A payload captured at click time would hand the sheet text the card has
    // since replaced — and the sheet writes back every field it is given.
    let release: (v: Todo) => void = () => {};
    updateTodoMock.mockImplementationOnce(() => new Promise<Todo>(res => { release = res; }));
    render();
    setValue(notesBox(), 'first');
    click(button('Edit'));
    setValue(notesBox(), 'first, then more');
    await act(async () => { release({ ...TODO, notes: 'first', updated_at: nextStamp() }); });
    await settle();

    expect(onEdit).toHaveBeenCalledTimes(1);
    expect(onEdit.mock.calls[0][0].notes).toBe('first, then more');
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

  it('does not rewind to the stale prop the moment the save lands', async () => {
    // The write's response is newer than the prop the parent is still holding. Deciding
    // adoption by CONTENT rather than by version reads that lagging prop as an outside
    // change, resets the box to the pre-save text, and lets the next blur write it back
    // over the save that just succeeded.
    render({ notes: 'before' });
    setValue(notesBox(), 'mine');
    unfocus(notesBox());
    await settle();
    expect(notesBox().value).toBe('mine');
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

describe('fields written straight through still reach the sheet', () => {
  // Star and project are never rendered optimistically, so adopting the WRITE RESPONSE is
  // the only thing that keeps them current. Without it the sheet can open on the pre-write
  // values once `busy` clears but before the refetch lands, and its full-row save reverts
  // the change the user just made.
  const star = () =>
    container.querySelector<HTMLButtonElement>('button[aria-label="Star as today priority"]')!;

  it('hands the sheet the star it just wrote', async () => {
    render({ star: false });
    click(star());
    await settle();
    click(button('Edit'));
    await settle();
    expect(onEdit.mock.calls[0][0].star).toBe(true);
  });

  it('hands the sheet a project it created mid-triage', async () => {
    // `createAndAssign` writes `project_id` through its own call rather than `patch`, and
    // `setBusy(false)` runs before the parent's reload resolves — so without adopting that
    // response too, Edit opens on the pre-assignment value and the sheet undoes the
    // assignment the user just made.
    render({ project_id: null });
    setValue(projectPicker(), 'new');
    setValue(newProjectInput(), 'Garage');
    submitForm(newProjectInput());
    await settle();

    click(button('Edit'));
    await settle();
    expect(onEdit.mock.calls[0][0].project_id).toBe(9);
  });

  it('hands the sheet the project it just assigned', async () => {
    render({ project_id: null });
    setValue(container.querySelector<HTMLSelectElement>('select[aria-label="Project"]')!, '3');
    await settle();
    click(button('Edit'));
    await settle();
    expect(onEdit.mock.calls[0][0].project_id).toBe(3);
  });

  it('toggles the star off the value it wrote, not the one the prop still shows', async () => {
    // Two taps before the refetch lands: reading the prop the second time would send the
    // same value again and leave the star stuck on.
    render({ star: false });
    click(star());
    await settle();
    click(star());
    await settle();
    expect(updateTodoMock).toHaveBeenNthCalledWith(1, 7, { star: true });
    expect(updateTodoMock).toHaveBeenNthCalledWith(2, 7, { star: false });
  });
});

describe('writes decide against the state as it is NOW', () => {
  // Every one of these runs a second write while a first is still in flight. A callback
  // created during one render closes over that render's values, so a handler that compared
  // against what it captured would be deciding on state that is arbitrarily old — and here
  // that is data loss, not just staleness.
  it('does not discard notes typed while another write was in flight', async () => {
    // The title save answers with a row whose notes are still the old ones. Comparing
    // against the draft captured when THAT request began sees an empty box, calls it clean,
    // and overwrites the paragraph typed since.
    let release: (v: Todo) => void = () => {};
    updateTodoMock.mockImplementationOnce(() => new Promise<Todo>(res => { release = res; }));
    render({ notes: '', title: 'dentist' });
    startTitleSave('dentist — reschedule');
    setValue(notesBox(), 'ring first');

    await act(async () => {
      release({ ...TODO, title: 'dentist — reschedule', notes: '', updated_at: nextStamp() });
    });
    await settle();

    expect(notesBox().value).toBe('ring first');
  });

  it('adopts a row that differs from the last only in microseconds', () => {
    // Date.parse truncates to milliseconds and the server keeps microseconds, so two commits
    // inside one millisecond compare equal — and equal means reject, which would drop the
    // newer row and leave the card on the older one for good.
    render({ notes: 'first', updated_at: '2026-08-06T12:00:00.123400+00:00' });
    render({ notes: 'second', updated_at: '2026-08-06T12:00:00.123900+00:00' });
    expect(notesBox().value).toBe('second');
  });

  it('breaks that tie on the fraction, not on the whole string', () => {
    // The SAME instant, written `+00:00` and `Z`, is the same version — and an equal version
    // is the echo this ordering exists to reject. A lexical compare of the whole timestamp
    // would call the second one newer, because `Z` sorts after `+`, and adopt it. Only the
    // fraction answers the question, and only it is free of how the zone is spelled.
    render({ notes: 'first', updated_at: '2026-08-06T12:00:00.123400+00:00' });
    render({ notes: 'second', updated_at: '2026-08-06T12:00:00.123400Z' });
    expect(notesBox().value).toBe('first');
  });

  it('offers each context once, however the shared list spells it', () => {
    // The meta list is the values other rows carry, so a legacy row with stray whitespace —
    // or two differing only by it — would put a near-duplicate in the picker and give two
    // options the same React key.
    render({ context: '@calls' }, [' @calls ', '@calls', '@errands']);
    const labels = [...contextPicker().options].map(o => o.textContent);
    expect(labels).toEqual(['Set context…', '@calls', '@errands', '+ New context…']);
  });

  it('does not re-send a note the server acknowledged', async () => {
    render({ notes: 'before' });
    setValue(notesBox(), 'mine');
    unfocus(notesBox());
    await settle();
    expect(updateTodoMock).toHaveBeenCalledTimes(1);

    unfocus(notesBox());
    await settle();
    expect(updateTodoMock).toHaveBeenCalledTimes(1);
  });

  it('re-sends a note a later write superseded', async () => {
    // `_now()` is stamped under the row's own FOR UPDATE lock, so a row NEWER than our
    // response was committed after our write. If it does not carry our text, ours was
    // replaced — and calling the box clean on the strength of the acknowledgement alone
    // would strand a paragraph the server does not have, silently and with no error.
    let release: (v: Todo) => void = () => {};
    updateTodoMock.mockImplementationOnce(() => new Promise<Todo>(res => { release = res; }));
    render({ notes: 'before', updated_at: '2026-08-06T12:00:00.000000+00:00' });
    setValue(notesBox(), 'mine');
    unfocus(notesBox());

    // Someone else's write lands first, and it does not carry our text.
    render({ notes: 'theirs', updated_at: '2026-08-06T12:00:09.000000+00:00' });
    await act(async () => {
      release({ ...TODO, notes: 'mine', updated_at: '2026-08-06T12:00:05.000000+00:00' });
    });
    await settle();

    expect(notesBox().value).toBe('mine');
    unfocus(notesBox());
    await settle();
    expect(updateTodoMock).toHaveBeenNthCalledWith(2, 7, { notes: 'mine' });
  });

  it('keeps an override while an EARLIER write of its own answers first', async () => {
    // A row is not evidence about a write still in flight. The notes write was sent first
    // and answers first, carrying the pre-rename title — releasing the override on that
    // disagreement flashes the old name back and hands it to the sheet, whose full-row save
    // then reverts the rename.
    let releaseNotes: (v: Todo) => void = () => {};
    updateTodoMock
      .mockImplementationOnce(() => new Promise<Todo>(res => { releaseNotes = res; }))
      .mockImplementationOnce(() => new Promise<Todo>(() => {}));
    render({ title: 'A', notes: 'before', updated_at: '2026-08-06T12:00:00.000000+00:00' });
    setValue(notesBox(), 'jot');
    unfocus(notesBox());   // the notes write goes out first
    startTitleSave('B');   // …and the rename goes out behind it, still in flight

    await act(async () => {
      releaseNotes({
        ...TODO, title: 'A', notes: 'jot', updated_at: '2026-08-06T12:00:05.000000+00:00',
      });
    });
    await settle();

    // The rename is still in flight, so its editor is still open (#232: the control stays
    // open until the write settles) — the pending value is what it shows, and what the sheet gets.
    expect(container.querySelector<HTMLInputElement>('input[aria-label="Todo title"]')!.value).toBe('B');
    click(button('Edit'));
    await settle();
    expect(onEdit.mock.calls[0][0].title).toBe('B');
  });

  it('still opens the sheet under StrictMode', async () => {
    // The app mounts under StrictMode, which runs an effect's setup, then its cleanup, then
    // setup again. A cleanup-only mounted flag is left false by that sequence, and the Edit
    // button then flushes the notes and silently does nothing — in every dev run.
    act(() => {
      root.render(
        <StrictMode>
          <TriageCard
            todo={TODO}
            projects={[PROJECT]}
            contexts={['@calls']}
            onProcessed={onProcessed}
            onChanged={onChanged}
            onEdit={onEdit}
          />
        </StrictMode>,
      );
    });
    setValue(notesBox(), 'jot');
    click(button('Edit'));
    await settle();

    expect(onEdit).toHaveBeenCalledTimes(1);
  });

  it('does not open the sheet after the card is swapped away', async () => {
    // Opening waits on the flush, and nothing gates the inbox queue meanwhile — promoting
    // another row unmounts this card, and the continuation would open the sheet on the todo
    // the user just navigated away from.
    let release: (v: Todo) => void = () => {};
    updateTodoMock.mockImplementationOnce(() => new Promise<Todo>(res => { release = res; }));
    render();
    setValue(notesBox(), 'jot');
    click(button('Edit'));
    act(() => { root.render(<div />); });

    await act(async () => { release({ ...TODO, notes: 'jot', updated_at: nextStamp() }); });
    await settle();

    expect(onEdit).not.toHaveBeenCalled();
  });

  it('opens the sheet on a star the flush response brought back', async () => {
    // The sheet payload has to be read when the sheet actually opens, not when the click
    // happened: the flush it waits on can answer with fields someone else changed, and
    // React has not committed that state by the time the continuation runs.
    let release: (v: Todo) => void = () => {};
    updateTodoMock.mockImplementationOnce(() => new Promise<Todo>(res => { release = res; }));
    render({ star: false });
    setValue(notesBox(), 'jot');
    click(button('Edit'));

    await act(async () => {
      release({ ...TODO, notes: 'jot', star: true, updated_at: nextStamp() });
    });
    await settle();

    expect(onEdit.mock.calls[0][0].star).toBe(true);
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
    updateTodoMock.mockImplementationOnce(() => new Promise<Todo>(res => { release = res; }));
    render();
    setValue(notesBox(), 'first');
    unfocus(notesBox());
    setValue(notesBox(), 'second');
    unfocus(notesBox());
    await settle();

    // Still only the first request — the second is queued, not racing.
    expect(updateTodoMock).toHaveBeenCalledTimes(1);
    await act(async () => { release({ ...TODO, notes: 'first', updated_at: nextStamp() }); });
    await settle();

    expect(updateTodoMock).toHaveBeenCalledTimes(2);
    expect(updateTodoMock).toHaveBeenNthCalledWith(2, 7, { notes: 'second' });
  });

  it('collapses three stacked commits into one trailing write', async () => {
    // Two callers chaining onto the SAME in-flight request wake on the same microtask and
    // both read a baseline React has not re-rendered yet, so each fires a write. Only
    // queueing on the tail of the chain — and moving the baseline synchronously — makes
    // the second and third collapse into one.
    let release: (v: Todo) => void = () => {};
    updateTodoMock.mockImplementationOnce(() => new Promise<Todo>(res => { release = res; }));
    render();
    setValue(notesBox(), 'first');
    unfocus(notesBox());          // starts the request
    setValue(notesBox(), 'second');
    unfocus(notesBox());          // queues
    unfocus(notesBox());          // queues again, behind the queue — not behind the request
    await settle();
    expect(updateTodoMock).toHaveBeenCalledTimes(1);

    await act(async () => { release({ ...TODO, notes: 'first', updated_at: nextStamp() }); });
    await settle();

    expect(updateTodoMock).toHaveBeenCalledTimes(2);
    expect(updateTodoMock).toHaveBeenNthCalledWith(2, 7, { notes: 'second' });
  });
});

describe("step 2 — the picked project's purpose and outcome (#262)", () => {
  const lines = () => container.querySelector('[data-project-purpose]');
  const PURPOSED: TodoProject = {
    ...PROJECT, purpose: 'A kitchen we can cook in', outcome: 'Cabinets installed',
  };

  it('shows nothing until a project is picked', () => {
    render({}, undefined, [PURPOSED]);
    expect(lines()).toBeNull();
  });

  it("shows the picked project's purpose and outcome, read-only", () => {
    render({ project_id: 3, project_name: 'Kitchen remodel' }, undefined, [PURPOSED]);
    expect(lines()?.textContent).toBe('PurposeA kitchen we can cook inOutcomeCabinets installed');
    expect(lines()?.querySelector('input, textarea, button')).toBeNull();
  });

  it('shows only the field that is set, and nothing for a project with neither', () => {
    render({ project_id: 3, project_name: 'Kitchen remodel' }, undefined, [{ ...PROJECT, outcome: 'Done' }]);
    expect(lines()?.textContent).toBe('OutcomeDone');
    render({ project_id: 3, project_name: 'Kitchen remodel' }, undefined, [PROJECT]);
    expect(lines()).toBeNull();
  });

  it('follows the picker when the project changes', async () => {
    render({}, undefined, [PURPOSED]);
    setValue(container.querySelector('select[aria-label="Project"]')!, '3');
    await settle();
    expect(lines()?.textContent).toContain('A kitchen we can cook in');
  });

  it('hides while a new project is being named', () => {
    render({ project_id: 3, project_name: 'Kitchen remodel' }, undefined, [PURPOSED]);
    setValue(container.querySelector('select[aria-label="Project"]')!, 'new');
    expect(lines()).toBeNull();
  });
});
