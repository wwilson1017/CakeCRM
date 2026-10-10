// @vitest-environment jsdom
//
// #282 — the working indicator names what the turn is doing and how long it has run,
// counted from the server's start time so a reattach or reload shows the true age.

import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { WorkingState } from './types';
import { WorkingIndicator } from './WorkingIndicator';

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-10-10T02:00:05Z'));
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

const render = (w: Partial<WorkingState>) => act(() => {
  root.render(createElement(WorkingIndicator, {
    working: { phase: 'model', startedAt: Date.parse('2026-10-10T02:00:00Z'), messageId: 'm', ...w },
  }));
});

describe('WorkingIndicator', () => {
  it.each([
    [{ phase: 'model' }, 'Working'],
    [{ phase: 'writing' }, 'Writing'],
    [{ phase: 'tool', tool: 'crm_list_deals' }, 'Running crm_list_deals'],
    [{ phase: 'reconnecting' }, 'Reconnecting…'],
    [{ phase: 'stopping' }, 'Stopping…'],
  ] as const)('labels %o as %s', (w, label) => {
    render(w);
    expect(container.querySelector('[role="status"]')?.textContent).toBe(`${label}0:05`);
  });

  it('ticks from the server start time', async () => {
    render({ startedAt: Date.parse('2026-10-10T01:58:55Z') });
    expect(container.textContent).toContain('1:10');
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(container.textContent).toContain('1:12');
  });
});
