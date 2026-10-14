// @vitest-environment jsdom
/**
 * RemindersPage (#283): the sweep request, void with a reason, and remind again.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { CrmReminder } from '../core/types';

const apiMock = vi.fn();
vi.mock('../core/api/client', () => ({
  api: (...args: unknown[]) => apiMock(...args),
  ApiError: class extends Error { status = 0; },
}));
vi.mock('../core/auth/AuthContext', () => ({ useAuth: () => ({ currentUser: { id: 3 } }) }));
vi.mock('./useUsers', () => {
  const users = [
    { id: 3, email: 'ada@example.test', name: 'Ada', role: 'admin', is_active: true },
    { id: 4, email: 'bo@example.test', name: 'Bo', role: 'member', is_active: true },
  ];
  return {
    useUsers: () => ({
      users, activeUsers: users, loading: false,
      nameFor: (id: number | null) => users.find(u => u.id === id)?.name ?? `User ${id}`,
    }),
  };
});

const { RemindersPage } = await import('./RemindersPage');

function reminder(over: Partial<CrmReminder> = {}): CrmReminder {
  return {
    id: 1, owner_id: 3, created_by: 3, note: 'Call the bakery', people: [3, 4],
    due_date: '2099-01-02', due_time: '15:00', due_at: '2099-01-02T21:00:00+00:00', repeat: null,
    anchor_date: '2099-01-02', series_id: null, status: 'upcoming', void_reason: '',
    fired_at: null, voided_at: null, created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z', ...over,
  };
}

let container: HTMLDivElement;
let root: Root;

async function settle(rounds = 10): Promise<void> {
  for (let i = 0; i < rounds; i++) await act(async () => { await Promise.resolve(); });
}

function button(text: string): HTMLButtonElement {
  const el = [...document.querySelectorAll('button')].find(b => b.textContent?.trim() === text);
  if (!el) throw new Error(`no button "${text}"`);
  return el as HTMLButtonElement;
}

function setValue(el: HTMLTextAreaElement | HTMLInputElement, value: string) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

async function mount(rows: CrmReminder[], onWrite: (url: string, init?: { body?: string }) => unknown) {
  apiMock.mockImplementation(async (url: string, init?: { body?: string }) => {
    if (String(url).endsWith('/gtd/filters')) return { tz: 'UTC' };
    return String(url).startsWith('/api/crm/reminders?') ? { reminders: rows } : onWrite(url, init);
  });
  act(() => { root.render(<RemindersPage />); });
  await settle();
}

async function openRow(note: string) {
  const cell = [...container.querySelectorAll('td, [role="row"] *')].find(e => e.textContent === note);
  await act(async () => { (cell as HTMLElement).click(); });
  await settle();
}

beforeEach(() => {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: false, media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  }));
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  apiMock.mockReset();
  sessionStorage.clear();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe('RemindersPage', () => {
  it('sweeps the corpus by id and lists each reminder with its people', async () => {
    await mount([reminder()], () => { throw new Error('no writes'); });
    const list = apiMock.mock.calls.map(c => String(c[0])).find(u => u.startsWith('/api/crm/reminders?'));
    expect(list).toContain('sort=id');
    expect(container.textContent).toContain('Call the bakery');
    expect(container.textContent).toContain('Ada, Bo');
  });

  it('defaults a new reminder to the INSTALL\'s day, not the browser\'s', async () => {
    // 03:00 UTC on the 14th is still the 13th in this test's pinned America/Chicago.
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-14T03:00:00Z'));
    try {
      await mount([], () => { throw new Error('no writes'); });
      await act(async () => { button('Add Reminder').click(); });
      expect((document.querySelector('#reminder-date') as HTMLInputElement).value).toBe('2026-10-14');
    } finally {
      vi.useRealTimers();
    }
  });

  it('voids with a reason and shows the row as voided', async () => {
    const writes: [string, unknown][] = [];
    await mount([reminder()], (url, init) => {
      writes.push([url, JSON.parse(init?.body ?? '{}')]);
      return reminder({ status: 'voided', void_reason: 'Done already' });
    });
    await openRow('Call the bakery');
    await act(async () => { button('Void').click(); });
    setValue(document.querySelector('#reminder-void-reason') as HTMLTextAreaElement, 'Done already');
    await act(async () => { button('Void Reminder').click(); });
    await settle();
    expect(writes).toEqual([['/api/crm/reminders/1/void', { reason: 'Done already' }]]);
    expect(container.textContent).toContain('Voided');
  });

  it('remind again posts only the new date and time, and adds the new row', async () => {
    const writes: [string, unknown][] = [];
    await mount([reminder({ status: 'fired' })], (url, init) => {
      writes.push([url, JSON.parse(init?.body ?? '{}')]);
      return reminder({ id: 2, note: 'Call the bakery', due_date: '2099-02-01', due_time: null });
    });
    await openRow('Call the bakery');
    expect(() => button('Void')).toThrow();  // a sent reminder is history
    await act(async () => { button('Remind again').click(); });
    setValue(document.querySelector('#reminder-date') as HTMLInputElement, '2099-02-01');
    await act(async () => { button('Set Reminder').click(); });
    await settle();
    expect(writes).toEqual([['/api/crm/reminders/1/remind-again', { due_date: '2099-02-01', due_time: null }]]);
    expect(container.querySelectorAll('tbody tr').length).toBe(2);
  });
});
