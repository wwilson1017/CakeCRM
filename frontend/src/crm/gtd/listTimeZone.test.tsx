// @vitest-environment jsdom
//
// #259 — "today" belongs to the INSTALL, not to the browser looking at it. The server's
// today_view picks rows by the configured TIMEZONE (it comes back on the filters
// payload as `tz`), and every GTD day decision on the client must agree — otherwise an
// evening user in another zone sees today's todos filed under Overdue.
//
// The first block is the issue's acceptance case verbatim: TIMEZONE=America/Chicago, a
// browser clock in UTC, 23:30 in Chicago. Node re-reads process.env.TZ when it is
// assigned, so the browser zone is switched for that block and restored after. The
// second block keeps the runner's own America/Chicago and puts the install AHEAD of it
// (Tokyo), so the opposite direction is pinned too. Every assertion fails if any date
// decision falls back to the browser's zone.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { todayTodosMock, createTodoMock, getFiltersMock } = vi.hoisted(() => ({
  todayTodosMock: vi.fn(),
  createTodoMock: vi.fn(),
  getFiltersMock: vi.fn(),
}));
vi.mock('./api', () => ({
  todayTodos: todayTodosMock,
  createTodo: createTodoMock,
  updateTodo: vi.fn(),
  deleteTodo: vi.fn(),
  listTodos: vi.fn().mockResolvedValue([]),
  listProjects: vi.fn().mockResolvedValue([]),
  getFilters: getFiltersMock,
}));

import { TodayPage } from './TodayPage';
import type { Todo } from './types';
import { resetUndoQueue } from './undoQueue';
import { __resetTodoMeta } from './useTodoMeta';
import { todayStr, zonedNow } from './util';

const base: Omit<Todo, 'id' | 'title' | 'due_date'> = {
  notes: '', project_id: null, project_name: null, context: '', tags: [],
  status: 'next_action', star: false, repeat: '',
  auto_star_on_due: false, source: 'ui',
  created_at: '2026-07-20T12:00:00Z', updated_at: '2026-07-20T12:00:00Z', completed_at: null,
  contact_id: null, deal_id: null,
};

let container: HTMLDivElement;
let root: Root;

function mount(now: Date, tz: string) {
  vi.useFakeTimers({ toFake: ['Date'] }); // only the clock: fetches resolve on real microtasks
  vi.setSystemTime(now);
  getFiltersMock.mockResolvedValue({ contexts: [], tags: [], status_counts: {}, tz });
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  resetUndoQueue();
  __resetTodoMeta();
  createTodoMock.mockReset().mockResolvedValue({});
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

async function renderToday() {
  await act(async () => {
    root.render(<MemoryRouter><TodayPage /></MemoryRouter>);
  });
  await act(async () => {}); // the filters payload (and its tz) lands
}

/** The titles under a section heading. */
function section(heading: string): string[] {
  const h2 = [...container.querySelectorAll('h2')].find(h => h.textContent?.trim() === heading);
  return [...(h2?.parentElement?.querySelectorAll('p.break-words') ?? [])]
    .map(p => p.textContent?.trim() ?? '');
}

function rowText(title: string): string {
  const p = [...container.querySelectorAll('p.break-words')].find(el => el.textContent?.includes(title));
  return p?.parentElement?.textContent ?? '';
}

async function quickAdd(text: string) {
  const input = container.querySelector<HTMLInputElement>('input[placeholder^="Add to inbox"]')!;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
  act(() => {
    setter.call(input, text);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await act(async () => {
    input.form!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
}

describe('acceptance: TIMEZONE=America/Chicago, browser in UTC, 23:30 Chicago', () => {
  // 23:30 on Aug 1 in Chicago; already 04:30 on Aug 2 in UTC.
  const NOW = new Date('2026-08-02T04:30:00Z');
  let savedTz: string | undefined;

  beforeEach(() => {
    savedTz = process.env.TZ;
    process.env.TZ = 'UTC';
    mount(NOW, 'America/Chicago');
  });
  afterEach(() => {
    process.env.TZ = savedTz;
  });

  it('the premise: the browser is already on the next day', () => {
    expect(todayStr(NOW)).toBe('2026-08-02');
    expect(todayStr(NOW, 'America/Chicago')).toBe('2026-08-01');
  });

  it('a todo due "today" renders under Due today, not Overdue', async () => {
    todayTodosMock.mockResolvedValue([
      { ...base, id: 1, title: 'due yesterday', due_date: '2026-07-31' },
      { ...base, id: 2, title: 'due today', due_date: '2026-08-01' },
    ]);
    await renderToday();

    expect(section('Overdue')).toEqual(['due yesterday']);
    expect(section('Due today')).toEqual(['due today']);
    // The row's own due chip agrees with the section it sits in.
    expect(rowText('due today')).toContain('Today');
  });

  it('quick-add "tomorrow" is the day after the install’s today', async () => {
    todayTodosMock.mockResolvedValue([]);
    await renderToday();
    await quickAdd('water the plants tomorrow');
    expect(createTodoMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'water the plants', due_date: '2026-08-02' }),
    );
  });
});

describe('an install AHEAD of the browser (Tokyo vs the runner’s Chicago)', () => {
  // 21:00 on Aug 1 in Chicago; 11:00 on Aug 2 in Tokyo.
  const NOW = new Date('2026-08-02T02:00:00Z');

  beforeEach(() => mount(NOW, 'Asia/Tokyo'));

  it('todayStr and zonedNow read the install’s date; an unknown zone falls back', () => {
    expect(todayStr(NOW)).toBe('2026-08-01');
    expect(todayStr(NOW, 'Asia/Tokyo')).toBe('2026-08-02');
    expect(todayStr(NOW, 'Not/AZone')).toBe('2026-08-01');
    const d = zonedNow('Asia/Tokyo', NOW);
    expect([d.getFullYear(), d.getMonth() + 1, d.getDate()]).toEqual([2026, 8, 2]);
  });

  it('Today buckets and labels by the install’s date', async () => {
    todayTodosMock.mockResolvedValue([
      { ...base, id: 1, title: 'due on the 1st', due_date: '2026-08-01' },
      { ...base, id: 2, title: 'due on the 2nd', due_date: '2026-08-02' },
    ]);
    await renderToday();

    expect(section('Overdue')).toEqual(['due on the 1st']);
    expect(section('Due today')).toEqual(['due on the 2nd']);
    expect(rowText('due on the 2nd')).toContain('Today');
  });

  it('quick-add "tomorrow" follows the install', async () => {
    todayTodosMock.mockResolvedValue([]);
    await renderToday();
    await quickAdd('water the plants tomorrow');
    expect(createTodoMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'water the plants', due_date: '2026-08-03' }),
    );
  });
});
