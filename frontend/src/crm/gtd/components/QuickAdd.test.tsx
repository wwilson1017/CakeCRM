// @vitest-environment jsdom
//
// Quick Add stays editable while an add is in flight (#292, port of todo-gtd 4e785ba), so
// landing that add may clear only the draft that was SENT — never what was typed since.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { createTodoMock } = vi.hoisted(() => ({ createTodoMock: vi.fn() }));

vi.mock('../api', () => ({
  createTodo: createTodoMock,
  getFilters: vi.fn().mockResolvedValue({ contexts: [], tags: [], status_counts: {} }),
  listProjects: vi.fn().mockResolvedValue([]),
}));

import { QuickAdd } from './QuickAdd';

let container: HTMLDivElement;
let root: Root;
const onAdded = vi.fn();

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  createTodoMock.mockReset();
  onAdded.mockReset();
  act(() => root.render(<QuickAdd onAdded={onAdded} />));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const box = () => container.querySelector<HTMLInputElement>('input[aria-label="Add to inbox"]')!;

function type(value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  act(() => {
    setter?.call(box(), value);
    box().dispatchEvent(new Event('input', { bubbles: true }));
  });
}

const submit = () => act(async () => {
  box().form!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
});

describe('QuickAdd', () => {
  it('keeps text typed while the previous add is still sending', async () => {
    let land!: () => void;
    createTodoMock.mockReturnValue(new Promise<void>(resolve => { land = resolve; }));
    type('Call the vet');
    await submit();
    type('Buy seed');
    await act(async () => { land(); });

    expect(createTodoMock).toHaveBeenCalledTimes(1);
    expect(onAdded).toHaveBeenCalledTimes(1);
    expect(box().value).toBe('Buy seed');
  });

  it('locks the chips and the auto-star box while an add is in flight', async () => {
    // Landing clears the chips' dismissals and the auto-star tick along with the text that
    // was sent, so a change made to either mid-request would be thrown away unsaved.
    let land!: () => void;
    createTodoMock.mockReturnValue(new Promise<void>(resolve => { land = resolve; }));
    type('Water the plants daily');
    const chip = () => container.querySelector<HTMLButtonElement>('button[title^="Remove"]')!;
    const autoStar = () => container.querySelector<HTMLInputElement>('input[type="checkbox"]')!;
    expect([chip().disabled, autoStar().disabled]).toEqual([false, false]);

    await submit();
    expect([chip().disabled, autoStar().disabled]).toEqual([true, true]);

    await act(async () => { land(); });
    expect(box().value).toBe('');
  });

  it('clears the box once the draft that was sent has landed', async () => {
    createTodoMock.mockResolvedValue({});
    type('Call the vet');
    await submit();

    expect(onAdded).toHaveBeenCalledTimes(1);
    expect(box().value).toBe('');
  });
});
