// @vitest-environment jsdom
//
// The composer's @ picker (#235): typing `@` lists active seats, a pick inserts the name
// and records the ID, the request carries IDs whose token is still in the text, and
// Escape closes the menu without leaking to a surrounding modal.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../core/api/client', () => ({
  api: vi.fn(async () => ({
    users: [
      { id: 1, email: 'ada@example.test', name: 'Ada Lovelace', role: 'admin', is_active: true },
      { id: 2, email: 'ann@example.test', name: 'Ann', role: 'member', is_active: true },
      { id: 3, email: 'gone@example.test', name: 'Annika', role: 'member', is_active: false },
    ],
  })),
}));

const { NoteComposer } = await import('./NoteComposer');

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const textarea = () => container.querySelector('textarea')!;
const options = () => Array.from(container.querySelectorAll('[role="option"]'));
const postButton = () =>
  Array.from(container.querySelectorAll('button')).find(b => /Post/.test(b.textContent!))!;

function type(value: string) {
  act(() => {
    const el = textarea();
    Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')!.set!.call(el, value);
    el.setSelectionRange(value.length, value.length);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

function key(k: string): KeyboardEvent {
  const e = new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true });
  act(() => { textarea().dispatchEvent(e); });
  return e;
}

async function renderComposer(onSubmit = vi.fn(async () => {})) {
  await act(async () => { root.render(<NoteComposer onSubmit={onSubmit} />); });
  await act(async () => { await Promise.resolve(); });  // let the roster land
  return onSubmit;
}

describe('NoteComposer @ mentions', () => {
  it('lists only ACTIVE seats and picks with Enter, inserting the name', async () => {
    const onSubmit = await renderComposer();
    type('call @an');
    expect(options().map(o => o.textContent)).toEqual(['Annann@example.test']);
    expect(textarea().getAttribute('aria-activedescendant')).toBe(options()[0].id);
    const e = key('Enter');
    expect(e.defaultPrevented).toBe(true);
    expect(textarea().value).toBe('call @Ann ');
    expect(options()).toHaveLength(0);

    type('call @Ann can you take this?');
    await act(async () => { postButton().click(); });
    expect(onSubmit).toHaveBeenCalledWith('call @Ann can you take this?', [], [2]);
  });

  it('drops a pick whose token was deleted before posting', async () => {
    const onSubmit = await renderComposer();
    type('@ad');
    key('Enter');
    expect(textarea().value).toBe('@Ada Lovelace ');
    type('never mind');
    await act(async () => { postButton().click(); });
    expect(onSubmit).toHaveBeenCalledWith('never mind', [], []);
  });

  it('arrows move the highlight and Escape closes only the menu', async () => {
    await renderComposer();
    type('@');
    expect(options()).toHaveLength(2);
    key('ArrowDown');
    expect(options()[1].getAttribute('aria-selected')).toBe('true');
    const esc = key('Escape');
    // preventDefault is what DetailModal's document Escape handler defers to.
    expect(esc.defaultPrevented).toBe(true);
    expect(options()).toHaveLength(0);
  });

  it('an email address in the text never opens the menu', async () => {
    await renderComposer();
    type('write to ada@exa');
    expect(options()).toHaveLength(0);
    // With the menu closed, plain Enter is left alone (a newline, per #57).
    expect(key('Enter').defaultPrevented).toBe(false);
  });
});

describe('NoteComposer @ mentions across a pending post', () => {
  it('keeps a pick made while the previous note was still posting', async () => {
    let release: () => void = () => {};
    const sent: number[][] = [];
    const onSubmit = vi.fn((_t: string, _f: File[], m: number[]) => {
      sent.push(m);
      return new Promise<void>(res => { release = res; });
    });
    await renderComposer(onSubmit);
    type('first note');
    await act(async () => { postButton().click(); });
    // While the first post is in flight, start the next note and pick Ann.
    type('@an');
    key('Enter');
    await act(async () => { release(); });
    expect(textarea().value).toBe('@Ann ');
    type('@Ann second');
    await act(async () => { postButton().click(); });
    expect(sent).toEqual([[], [2]]);
  });
});
