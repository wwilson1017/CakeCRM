// @vitest-environment jsdom
//
// The bulk-create Approve card (#284) lists every item, collapsed past COLLAPSED_ROWS.

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { BulkItemList, COLLAPSED_ROWS, bulkItems } from './BulkItemList';

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

const todos = (n: number) =>
  Array.from({ length: n }, (_, i) => ({ title: `Item ${i + 1}`, project: 'Garden', context: '@home' }));
const rows = () => [...container.querySelectorAll('li')].map((li) => li.textContent ?? '');
const button = () => container.querySelector('button');

function render(items: unknown[]) {
  act(() => root.render(<BulkItemList items={items} />));
}

describe('bulkItems', () => {
  it('reads a todos array and ignores every other call shape', () => {
    expect(bulkItems({ todos: [{ title: 'a' }] })).toEqual([{ title: 'a' }]);
    expect(bulkItems({ title: 'a' })).toBeNull();
    expect(bulkItems({ todos: 'a' })).toBeNull();
    expect(bulkItems(undefined)).toBeNull();
  });
});

describe('BulkItemList', () => {
  it('lists a short batch in full with no expander', () => {
    render(todos(3));
    expect(rows()).toEqual([
      'Item 1 · Garden · @home', 'Item 2 · Garden · @home', 'Item 3 · Garden · @home',
    ]);
    expect(button()).toBeNull();
    expect(container.textContent).toContain('3 items');
  });

  it('collapses a long batch and expands it on Show all', () => {
    render(todos(30));
    expect(rows()).toHaveLength(COLLAPSED_ROWS);
    expect(button()?.textContent).toBe('Show all 30');
    act(() => button()!.click());
    expect(rows()).toHaveLength(30);
    expect(rows()[29]).toContain('Item 30');
    expect(button()?.textContent).toBe('Show fewer');
  });

  it('survives an item that is not an object', () => {
    render(['plain text', { notes: 'no title' }]);
    expect(rows()).toEqual(['plain text', '(no title)']);
  });
});
