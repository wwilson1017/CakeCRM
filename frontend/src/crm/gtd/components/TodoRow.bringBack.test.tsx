// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { TodoRow } from './TodoRow';
import type { Todo } from '../types';

// The bring-back date (#261) on a row: before its day the row is only reachable through
// search, so it says when it comes back; from that day it says it is back — and it is
// never painted as overdue, because only a due date can make a todo overdue.

const TODO: Todo = {
  id: 1, title: 'check back after Q1 budget', notes: '', project_id: null, project_name: null,
  context: '', tags: [], status: 'next_action', star: false, due_date: '', repeat: '',
  auto_star_on_due: false, source: 'ui', created_at: '2026-10-01T12:00:00Z',
  updated_at: '2026-10-01T12:00:00Z', completed_at: null, contact_id: null, deal_id: null,
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers({ toFake: ['Date'] });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

function renderOn(day: string, todo: Partial<Todo>) {
  vi.setSystemTime(new Date(`${day}T12:00:00`));
  act(() => {
    root.render(<TodoRow todo={{ ...TODO, ...todo }} onToggleDone={() => {}}
                         onToggleStar={() => {}} onEdit={() => {}} />);
  });
  return container.querySelector<HTMLElement>('[data-bring-back]');
}

describe('a todo with a bring-back date', () => {
  it('says when it comes back while it is waiting', () => {
    const chip = renderOn('2026-10-04', { bring_back_on: '2026-10-05' });
    expect(chip?.dataset.bringBack).toBe('waiting');
    expect(chip?.textContent).toBe('Back Tomorrow');
  });

  it('turns into "Brought back" on its day, and stays that way after it', () => {
    expect(renderOn('2026-10-05', { bring_back_on: '2026-10-05' })?.textContent).toBe('Brought back');
    const late = renderOn('2026-11-20', { bring_back_on: '2026-10-05' });
    expect(late?.textContent).toBe('Brought back');
    // Never painted as overdue: with no due date there is no overdue styling at all.
    expect(container.querySelector('.text-ck-accent-text')).toBeNull();
  });

  it('shows nothing once the todo is finished', () => {
    expect(renderOn('2026-10-04', { bring_back_on: '2026-10-05', status: 'done' })).toBeNull();
  });
});
