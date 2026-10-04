// @vitest-environment jsdom
//
// Previous / next project on the project detail page (#264, port of todo-gtd 382ce21).
//
// The ordering rules are pinned in `projectNav.test.ts`; this file pins what only the page
// can show: that the ‹ › links carry accessible names naming their destination, that the
// arrow keys navigate (wrapping at both ends), that they do NOTHING while focus is in a
// field — the #232 inline editors above all — and that landing on a project shows THAT
// project rather than the previous one's state.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { listProjectsMock, listTodosMock, getFiltersMock } = vi.hoisted(() => ({
  listProjectsMock: vi.fn(),
  listTodosMock: vi.fn(),
  getFiltersMock: vi.fn(),
}));

vi.mock('./api', () => ({
  listTodos: listTodosMock,
  todayTodos: vi.fn().mockResolvedValue([]),
  createTodo: vi.fn(),
  updateTodo: vi.fn(),
  deleteTodo: vi.fn(),
  bulkUpdate: vi.fn(),
  listProjects: listProjectsMock,
  createProject: vi.fn(),
  updateProject: vi.fn(),
  deleteProject: vi.fn(),
  getFilters: getFiltersMock,
}));

import { ProjectDetailPage } from './ProjectDetailPage';
import { __resetTodoMeta } from './useTodoMeta';
import type { TodoProject } from './types';

const P = (id: number, name: string, status: TodoProject['status'] = 'active'): TodoProject => ({
  id, name, notes: '', purpose: '', outcome: '', status, open_count: 0,
  created_at: '2026-09-01T12:00:00Z', updated_at: '2026-09-01T12:00:00Z',
});

// Server order (lower(name)); one completed project sits in the middle and must be skipped.
const PROJECTS = [P(3, 'Alpha'), P(8, 'Bravo', 'completed'), P(7, 'Charlie'), P(5, 'Delta')];

let container: HTMLDivElement;
let root: Root;
function PathProbe() {
  return <output data-path>{useLocation().pathname}</output>;
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  listProjectsMock.mockReset().mockResolvedValue(PROJECTS);
  listTodosMock.mockReset().mockResolvedValue([]);
  getFiltersMock.mockReset().mockResolvedValue({ contexts: [], tags: [], status_counts: {} });
  __resetTodoMeta();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  __resetTodoMeta();
});

async function render(id: number) {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[`/crm/todos/projects/${id}`]}>
        <PathProbe />
        <Routes>
          <Route path="/crm/todos/projects/:id" element={<ProjectDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );
  });
  await settle();
}

async function settle() {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

const link = (label: string) =>
  container.querySelector<HTMLAnchorElement>(`a[aria-label^="${label}"]`);

async function key(target: EventTarget, k: string, init: KeyboardEventInit = {}) {
  await act(async () => {
    target.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, ...init }));
  });
  await settle();
}

const path = () => container.querySelector('[data-path]')?.textContent;
const heading = () => container.querySelector('h2')?.textContent;

describe('ProjectDetailPage — previous / next project (#264)', () => {
  it('names each link after its destination, skipping other statuses', async () => {
    await render(7);
    expect(link('Previous project')?.getAttribute('aria-label')).toBe('Previous project: Alpha');
    expect(link('Next project')?.getAttribute('aria-label')).toBe('Next project: Delta');
    expect(link('Next project')?.getAttribute('href')).toBe('/crm/todos/projects/5');
  });

  it('wraps around at both ends', async () => {
    await render(5);
    expect(link('Next project')?.getAttribute('aria-label')).toBe('Next project: Alpha');
    expect(link('Previous project')?.getAttribute('aria-label')).toBe('Previous project: Charlie');
  });

  it('renders no arrows when the project is the only one of its status', async () => {
    await render(8);
    expect(link('Previous project')).toBeNull();
    expect(link('Next project')).toBeNull();
  });

  it('moves with the arrow keys and lands on the new project', async () => {
    await render(7);
    expect(heading()).toContain('Charlie');
    await key(document.body, 'ArrowRight');
    expect(path()).toBe('/crm/todos/projects/5');
    expect(heading()).toContain('Delta');
    await key(document.body, 'ArrowRight');
    expect(path()).toBe('/crm/todos/projects/3');
    await key(document.body, 'ArrowLeft');
    expect(path()).toBe('/crm/todos/projects/5');
  });

  it("never shows the previous project's state while the next one loads", async () => {
    await render(7);
    expect(heading()).toContain('Charlie');
    listTodosMock.mockReturnValue(new Promise(() => {}));
    await key(document.body, 'ArrowRight');
    expect(path()).toBe('/crm/todos/projects/5');
    expect(heading()).toBeUndefined();
  });

  it('ignores arrow keys typed into a field, including the inline name editor', async () => {
    await render(7);
    act(() => container.querySelector<HTMLElement>('span[title="Click to rename"]')!.click());
    const editor = container.querySelector<HTMLInputElement>('input[aria-label="Project name"]')!;
    expect(editor).not.toBeNull();
    await key(editor, 'ArrowRight');
    expect(path()).toBe('/crm/todos/projects/7');
    const addBox = container.querySelector<HTMLInputElement>('input[aria-label="Add a next action"]')!;
    await key(addBox, 'ArrowLeft');
    expect(path()).toBe('/crm/todos/projects/7');
  });

  it('ignores chorded arrows', async () => {
    await render(7);
    for (const chord of [{ altKey: true }, { metaKey: true }, { ctrlKey: true }, { shiftKey: true }]) {
      await key(document.body, 'ArrowRight', chord);
      expect(path()).toBe('/crm/todos/projects/7');
    }
  });
});
