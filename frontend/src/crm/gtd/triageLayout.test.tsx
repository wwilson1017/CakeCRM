// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import { readTriageLayout, saveTriageLayout, TRIAGE_LAYOUT_KEY, useTriageLayout } from './triageLayout';

afterEach(() => {
  vi.restoreAllMocks();
  saveTriageLayout('compact');
});

it('defaults to compact and stores only the expanded preference', () => {
  localStorage.removeItem(TRIAGE_LAYOUT_KEY);
  expect(readTriageLayout()).toBe('compact');
  saveTriageLayout('expanded');
  expect(readTriageLayout()).toBe('expanded');
  expect(localStorage.getItem(TRIAGE_LAYOUT_KEY)).toBe('expanded');
  saveTriageLayout('compact');
  expect(localStorage.getItem(TRIAGE_LAYOUT_KEY)).toBeNull();
});

it('updates subscribers even when device storage is blocked', () => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('blocked'); });
  expect(readTriageLayout()).toBe('compact');
  const container = document.createElement('div');
  const root = createRoot(container);
  function Reader() {
    return <span>{useTriageLayout()[0]}</span>;
  }
  try {
    act(() => { root.render(<><Reader /><Reader /></>); });
    act(() => saveTriageLayout('expanded'));
    expect(container.textContent).toBe('expandedexpanded');
    act(() => saveTriageLayout('compact'));
    expect(container.textContent).toBe('compactcompact');
  } finally {
    act(() => root.unmount());
  }
});
