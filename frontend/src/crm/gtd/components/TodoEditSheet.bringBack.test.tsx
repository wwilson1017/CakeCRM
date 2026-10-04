// @vitest-environment jsdom
//
// The bring-back date (#261) on the full editor: seeded from the todo, written on Save,
// cleared to null (the column's "no date"), and not offered while creating a todo —
// only the update path writes it.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

const { updateTodoMock } = vi.hoisted(() => ({ updateTodoMock: vi.fn() }));
vi.mock('../api', () => ({
  createTodo: vi.fn(),
  updateTodo: updateTodoMock,
  deleteTodo: vi.fn(),
}));

import { TodoEditSheet } from './TodoEditSheet';
import type { Todo } from '../types';

const TODO: Todo = {
  id: 7,
  title: 'Call the plumber',
  notes: '',
  project_id: null,
  project_name: null,
  context: '@calls',
  tags: [],
  status: 'next_action',
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

let container: HTMLDivElement;
let root: Root;
let onSaved: Mock<() => void>;
let onClose: Mock<() => void>;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  updateTodoMock.mockReset().mockResolvedValue(TODO);
  onSaved = vi.fn();
  onClose = vi.fn();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(todo: Partial<Todo> | null = {}, contexts = ['@calls', '@errands']) {
  act(() => {
    root.render(
      <TodoEditSheet
        todo={todo === null ? null : { ...TODO, ...todo }}
        projects={[]}
        contexts={contexts}
        onClose={onClose}
        onSaved={onSaved}
      />,
    );
  });
}

/** React tracks the value it last rendered, so a plain `el.value = x` is invisible to
 * it — go through the prototype setter the way React's own tracker does. */
function setValue(el: HTMLSelectElement | HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')?.set;
  act(() => {
    setter?.call(el, value);
    el.dispatchEvent(
      new Event(el instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }),
    );
  });
}

async function save() {
  const btn = [...container.querySelectorAll('button')].find(b => b.textContent?.trim() === 'Save')!;
  act(() => { btn.click(); });
  await act(async () => {});
  return updateTodoMock.mock.calls.at(-1)?.[1] as Record<string, unknown>;
}

const backInput = () => container.querySelector<HTMLInputElement>('#gtd-bring-back');

describe('the bring-back date on the edit sheet', () => {
  it('shows the stored date and saves a new one', async () => {
    render({ bring_back_on: '2026-11-02' });
    expect(backInput()!.value).toBe('2026-11-02');
    setValue(backInput()!, '2027-01-15');
    expect((await save()).bring_back_on).toBe('2027-01-15');
  });

  it('clears to null', async () => {
    render({ bring_back_on: '2026-11-02' });
    setValue(backInput()!, '');
    expect((await save()).bring_back_on).toBeNull();
  });

  it('is not offered while creating a todo', () => {
    render(null);
    expect(backInput()).toBeNull();
  });
});
