// @vitest-environment jsdom
//
// The Review page's "review due" hint and its Mark review done button (#263).
//
// The hint is what makes the weekly review a habit rather than a page nobody opens, so
// what is pinned is that it appears exactly when the server says a review is due, that the
// button clears it with the server's answer rather than an optimistic guess, and that a
// failed save says so instead of quietly leaving the hint where it was.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { getReviewStatusMock, markReviewDoneMock } = vi.hoisted(() => ({
  getReviewStatusMock: vi.fn(),
  markReviewDoneMock: vi.fn(),
}));

vi.mock('./api', () => ({
  listTodos: vi.fn().mockResolvedValue([]),
  todayTodos: vi.fn().mockResolvedValue([]),
  createTodo: vi.fn(),
  updateTodo: vi.fn(),
  deleteTodo: vi.fn(),
  bulkUpdate: vi.fn(),
  listProjects: vi.fn().mockResolvedValue([]),
  createProject: vi.fn(),
  updateProject: vi.fn(),
  deleteProject: vi.fn(),
  getFilters: vi.fn().mockResolvedValue({ contexts: [], tags: [], status_counts: {} }),
  getReviewStatus: getReviewStatusMock,
  markReviewDone: markReviewDoneMock,
}));

import { ReviewPage } from './ReviewPage';
import { __resetTodoMeta } from './useTodoMeta';

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  getReviewStatusMock.mockReset();
  markReviewDoneMock.mockReset();
  __resetTodoMeta();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  __resetTodoMeta();
});

async function mount() {
  await act(async () => {
    root.render(<MemoryRouter><ReviewPage /></MemoryRouter>);
  });
  await act(async () => { await Promise.resolve(); });
}

const text = () => container.textContent ?? '';
const markButton = () =>
  [...container.querySelectorAll('button')].find(b => b.textContent === 'Mark review done');

describe('the review-due hint', () => {
  it('says a review is due when none was ever recorded', async () => {
    getReviewStatusMock.mockResolvedValue({ review_due: true, days_since_review: null });
    await mount();
    expect(container.querySelector('[role="status"]')?.textContent)
      .toBe('Review due — none recorded yet');
  });

  it('says how long it has been when one is overdue', async () => {
    getReviewStatusMock.mockResolvedValue({ review_due: true, days_since_review: 12 });
    await mount();
    expect(text()).toContain('Review due — last one 12 days ago');
  });

  it('shows no hint while a review is fresh', async () => {
    getReviewStatusMock.mockResolvedValue({ review_due: false, days_since_review: 1 });
    await mount();
    expect(text()).not.toContain('Review due');
    expect(text()).toContain('Last review yesterday.');
  });

  it('marking the review done clears the hint with the server answer', async () => {
    getReviewStatusMock.mockResolvedValue({ review_due: true, days_since_review: null });
    markReviewDoneMock.mockResolvedValue({ review_due: false, days_since_review: 0 });
    await mount();
    await act(async () => { markButton()!.click(); });
    expect(markReviewDoneMock).toHaveBeenCalledTimes(1);
    expect(text()).not.toContain('Review due');
    expect(text()).toContain('Last review today.');
  });

  it('a failed save says so and keeps the hint', async () => {
    getReviewStatusMock.mockResolvedValue({ review_due: true, days_since_review: 9 });
    markReviewDoneMock.mockRejectedValue(new Error('API error 500'));
    await mount();
    await act(async () => { markButton()!.click(); });
    expect(container.querySelector('[role="alert"]')?.textContent).toBe('Could not save. Try again.');
    expect(text()).toContain('Review due');
  });

  it('renders the lists even when the review clock cannot be read', async () => {
    getReviewStatusMock.mockRejectedValue(new Error('API error 500'));
    await mount();
    expect(markButton()).toBeUndefined();
    expect(text()).toContain('Active projects without a next action');
  });
});
