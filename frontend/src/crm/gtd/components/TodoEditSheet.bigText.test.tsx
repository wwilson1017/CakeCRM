// @vitest-environment jsdom
//
// #305: the sheet's Notes box opens the larger editor from its "Open larger" link and from
// a click into a box that already overflows, hands the edit back on Save, and Esc closes
// only the editor, never the sheet underneath. `BigTextEditor.test.tsx` proves the editor;
// this proves the wiring.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', () => ({ createTodo: vi.fn(), updateTodo: vi.fn(), deleteTodo: vi.fn() }));

import { TodoEditSheet } from './TodoEditSheet';
import type { Todo } from '../types';

const TODO: Todo = {
  id: 7, title: 'Write the brief', notes: 'line one', project_id: null, project_name: null,
  context: '', tags: [], status: 'next_action', star: false, due_date: '', repeat: '',
  auto_star_on_due: false, source: 'web', created_at: '2026-08-06T12:00:00Z',
  updated_at: '2026-08-06T12:00:00Z', completed_at: null, contact_id: null, deal_id: null,
};

let container: HTMLDivElement;
let root: Root;
let onClose: ReturnType<typeof vi.fn<() => void>>;

beforeEach(async () => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  onClose = vi.fn<() => void>();
  await act(async () => {
    root.render(<TodoEditSheet todo={TODO} projects={[]} contexts={[]} onClose={onClose} onSaved={() => {}} />);
  });
});
afterEach(() => { act(() => root.unmount()); container.remove(); });

const notesBox = () => container.querySelector<HTMLTextAreaElement>('#gtd-notes')!;
const dialog = () => container.querySelector('[role="dialog"]');
const button = (label: string) =>
  [...container.querySelectorAll('button')].find(b => b.textContent?.trim() === label) as HTMLButtonElement;

/** jsdom lays nothing out, so give the box the heights a browser would measure. */
function measure(scrollHeight: number, clientHeight: number) {
  Object.defineProperty(notesBox(), 'scrollHeight', { configurable: true, value: scrollHeight });
  Object.defineProperty(notesBox(), 'clientHeight', { configurable: true, value: clientHeight });
}

describe('TodoEditSheet larger notes editor', () => {
  it('opens on a click only when the notes box overflows', () => {
    measure(80, 80);
    act(() => { notesBox().click(); });
    expect(dialog()).toBeNull();
    measure(240, 80);
    act(() => { notesBox().click(); });
    expect(dialog()).not.toBeNull();
  });

  it('opens from the link and hands the edited text back on Save', () => {
    act(() => { button('Open larger ⤢').click(); });
    const big = dialog()!.querySelector('textarea')!;
    expect(big.value).toBe('line one');
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(big, 'line one\nline two');
      big.dispatchEvent(new Event('input', { bubbles: true }));
    });
    const save = [...dialog()!.querySelectorAll('button')].find(b => b.textContent?.trim() === 'Save')!;
    act(() => { save.click(); });
    expect(dialog()).toBeNull();
    expect(notesBox().value).toBe('line one\nline two');
  });

  it('closes only the editor on Escape', () => {
    act(() => { button('Open larger ⤢').click(); });
    act(() => { document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })); });
    expect(dialog()).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    expect(notesBox().value).toBe('line one');
  });
});
