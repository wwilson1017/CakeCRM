// `listTodos({ all: true })` — the sweep that reads a list past the 500-row window (#295).
//
// The fake server below honours `sort=id`, `after_id` and `limit` the way the real route
// does, so what is pinned is the WALK: every row exactly once, the cursor taken from the
// last KEPT row, one extra row asked for instead of a count, and the result handed back in
// the lists' own oldest-first order rather than the id order it was fetched in.
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }));
vi.mock('../../core/api/client', () => ({ api: apiMock }));
vi.mock('./publicMode', () => ({ TODO_API_BASE: '/api/crm/gtd', isTodoPublicMode: false }));

import { listTodos } from './api';

interface Row { id: number; created_at: string }

const query = (call: unknown[]) => new URL(String(call[0]), 'http://test').searchParams;

function serve(rows: Row[]) {
  apiMock.mockImplementation(async (path: string) => {
    const q = query([path]);
    const after = Number(q.get('after_id') ?? 0);
    const ordered = q.get('sort') === 'id' ? [...rows].sort((a, b) => a.id - b.id) : rows;
    return { todos: ordered.filter(r => r.id > after).slice(0, Number(q.get('limit') ?? 200)) };
  });
}

const at = (second: number) => `2026-09-01T12:00:${String(second).padStart(2, '0')}Z`;

beforeEach(() => { apiMock.mockReset(); });

describe('listTodos({ all: true })', () => {
  it('reads a list past the 500-row window, every row exactly once', async () => {
    serve(Array.from({ length: 1100 }, (_, i) => ({ id: i + 1, created_at: at(0) })));

    const todos = await listTodos({ status: 'inbox', all: true });

    expect(todos.map(t => t.id)).toEqual(Array.from({ length: 1100 }, (_, i) => i + 1));
    const calls = apiMock.mock.calls.map(query);
    // One row more than a page keeps, and the cursor is the last KEPT row — so the surplus
    // row is re-read as the head of the next page, never skipped.
    expect(calls.map(q => q.get('after_id'))).toEqual([null, '499', '998']);
    for (const q of calls) {
      expect([q.get('status'), q.get('sort'), q.get('limit'), q.get('all')])
        .toEqual(['inbox', 'id', '500', null]);
    }
  });

  it('hands the list back oldest first, not in the id order it was fetched in', async () => {
    // A backdated row (the sample data has them) and two rows from one transaction.
    serve([{ id: 1, created_at: at(30) }, { id: 2, created_at: at(10) }, { id: 3, created_at: at(10) }]);

    const todos = await listTodos({ status: 'next_action', all: true });

    expect(todos.map(t => t.id)).toEqual([2, 3, 1]);
    expect(apiMock).toHaveBeenCalledTimes(1);
  });

  it('fails instead of spinning when the server ignores the cursor', async () => {
    const page = Array.from({ length: 500 }, (_, i) => ({ id: i + 1, created_at: at(0) }));
    apiMock.mockResolvedValue({ todos: page });

    await expect(listTodos({ status: 'inbox', all: true })).rejects.toThrow('did not advance');
    expect(apiMock).toHaveBeenCalledTimes(2);
  });
});

describe('listTodos', () => {
  it('is still one window without `all`', async () => {
    serve([{ id: 1, created_at: at(0) }]);

    await listTodos({ status: 'done', limit: 200 });

    expect(apiMock).toHaveBeenCalledTimes(1);
    expect(apiMock.mock.calls[0][0]).toBe('/api/crm/gtd/todos?status=done&limit=200');
  });
});
