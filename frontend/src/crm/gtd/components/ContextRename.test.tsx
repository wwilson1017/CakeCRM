// @vitest-environment jsdom
//
// The rename → conflict → merge flow (#280). None of it is visible to tsc: that a 409
// arms Merge with the server's sentence and writes nothing, that Merge resends with
// `merge: true`, and that an armed Merge disarms the moment the name changes (an answer
// for "@calls" must never merge "@call").
//
// createRoot + React act, following the repo's other component tests — no RTL.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../../core/api/client';

const { renameMock } = vi.hoisted(() => ({ renameMock: vi.fn() }));
vi.mock('../api', () => ({ renameContext: renameMock }));

import { ContextRename } from './ContextRename';

let container: HTMLDivElement;
let root: Root;
let renamed: string[];

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  renamed = [];
  renameMock.mockReset();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const button = (label: string) =>
  [...container.querySelectorAll('button')].find(b => b.textContent === label) as HTMLButtonElement | undefined;
const input = () => container.querySelector('input') as HTMLInputElement;

function type(value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
  act(() => {
    setter.call(input(), value);
    input().dispatchEvent(new Event('input', { bubbles: true }));
  });
}

async function click(label: string) {
  await act(async () => { button(label)!.click(); });
}

function mount() {
  act(() => root.render(<ContextRename context="@phone" onRenamed={n => renamed.push(n)} />));
}

const CONFLICT = 'You already have a context named "@calls". Merging moves every "@phone" todo into it.';

describe('ContextRename', () => {
  it('renames straight through when the new name is free', async () => {
    renameMock.mockResolvedValue({ count: 2, todo_ids: [1, 2], merged: false });
    mount();
    await click('Rename');
    type('  @calls ');
    await click('Save');
    expect(renameMock).toHaveBeenCalledWith('@phone', '@calls', false);
    expect(renamed).toEqual(['@calls']);
    expect(input()).toBeNull();
  });

  it('shows the conflict with Merge on a 409, then resends with merge', async () => {
    renameMock.mockRejectedValueOnce(new ApiError('API error 409', 409, CONFLICT));
    renameMock.mockResolvedValueOnce({ count: 1, todo_ids: [1], merged: true });
    mount();
    await click('Rename');
    type('@calls');
    await click('Save');
    expect(renamed).toEqual([]);
    expect(container.querySelector('[role="alert"]')?.textContent).toBe(CONFLICT);
    expect(button('Save')).toBeUndefined();

    await click('Merge');
    expect(renameMock).toHaveBeenLastCalledWith('@phone', '@calls', true);
    expect(renamed).toEqual(['@calls']);
  });

  it('disarms Merge once the name is edited', async () => {
    renameMock.mockRejectedValueOnce(new ApiError('API error 409', 409, CONFLICT));
    mount();
    await click('Rename');
    type('@calls');
    await click('Save');
    expect(button('Merge')).toBeDefined();
    type('@call');
    expect(button('Merge')).toBeUndefined();
    expect(button('Save')).toBeDefined();
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it('shows other failures as an error and never arms Merge', async () => {
    renameMock.mockRejectedValueOnce(new ApiError('API error 404', 404, 'Not found'));
    mount();
    await click('Rename');
    type('@calls');
    await click('Save');
    expect(button('Merge')).toBeUndefined();
    expect(container.querySelector('[role="alert"]')?.textContent).toBe('Not found');
    expect(renamed).toEqual([]);
  });

  it('refuses an empty name without calling the server', async () => {
    mount();
    await click('Rename');
    type('   ');
    await click('Save');
    expect(renameMock).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alert"]')?.textContent).toBe('A context name is required.');
  });
});
