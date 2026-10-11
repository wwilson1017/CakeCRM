// @vitest-environment jsdom
//
// The header search box on a deep link (#292, port of todo-gtd 3c2a3a6). TodoShell mounts
// fresh on every page, so landing on /search?q=… is a first render, not a URL change: the
// box has to pick the query up there too, or it sits blank over a page of results and the
// next submit searches for nothing.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./api', () => ({
  createTodo: vi.fn(),
  updateTodo: vi.fn(),
  getFilters: vi.fn().mockResolvedValue({ contexts: [], tags: [], status_counts: {} }),
  listProjects: vi.fn().mockResolvedValue([]),
}));

import { TodoShell, type TodoTab } from './TodoShell';
import { __resetTodoMeta } from './useTodoMeta';

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  __resetTodoMeta();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  __resetTodoMeta();
});

async function boxAt(url: string, active: TodoTab): Promise<string> {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[url]}>
        <TodoShell active={active} hideQuickAdd>{null}</TodoShell>
      </MemoryRouter>,
    );
  });
  return container.querySelector<HTMLInputElement>('input[aria-label="Search all todos"]')!.value;
}

describe('TodoShell header search box', () => {
  it('shows the query of a deep-linked results page', async () => {
    expect(await boxAt('/crm/todos/search?q=caterer', 'search')).toBe('caterer');
  });

  it('stays blank on every other page', async () => {
    expect(await boxAt('/crm/todos/inbox?q=caterer', 'inbox')).toBe('');
  });
});
