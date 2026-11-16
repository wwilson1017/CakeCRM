// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const { api, success, error } = vi.hoisted(() => ({ api: vi.fn(), success: vi.fn(), error: vi.fn() }));
vi.mock('../../core/api/client', () => ({ api, ApiError: class ApiError extends Error {
  detail: string;
  constructor(message: string, _status: number, detail: string) { super(message); this.detail = detail; }
} }));
vi.mock('../../core/auth/AuthContext', () => ({ useAuth: () => ({ isAdmin: true }) }));
vi.mock('../../shared/toast', () => ({ toast: { success, error } }));
vi.mock('../../core/notifications/pushSubscription', () => ({
  isPushSupported: () => false, getPushPermissionState: () => 'unsupported',
  isSubscribed: async () => false, subscribeToPush: vi.fn(), unsubscribeFromPush: vi.fn(),
}));

const { NotificationSettings } = await import('./NotificationSettings');
const { ApiError } = await import('../../core/api/client');
const defaults = { enabled: true, interval_minutes: 30, quiet_hours: 'off', min_gap_hours: 4,
  paused_until: null as string | null, timezone: 'UTC', operator_enabled: true };
let hb = { ...defaults };
let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  hb = { ...defaults };
  vi.clearAllMocks();
  api.mockImplementation(async (path: string, options?: { body: string }) => {
    if (path === '/api/heartbeat/status') return { state: { proactive_enabled: true },
      proactive_digest_hour: 8, provider_ready: true, heartbeat_settings: hb };
    if (path === '/api/heartbeat/settings') {
      hb = { ...hb, ...JSON.parse(options!.body) };
      return { settings: hb };
    }
    throw new Error(`Unexpected endpoint: ${path}`);
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render() {
  await act(async () => { root.render(<NotificationSettings isMobile={false} />); });
}
async function click(label: string) {
  const button = [...container.querySelectorAll('button')].find(b => b.textContent === label);
  if (!button) throw new Error(`Missing button ${label}`);
  await act(async () => { button.click(); });
}
async function change(element: HTMLInputElement | HTMLSelectElement, value: string) {
  const prototype = element instanceof HTMLInputElement ? HTMLInputElement.prototype : HTMLSelectElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(element, value);
    element.dispatchEvent(new Event('input', { bubbles: true }));
    element.dispatchEvent(new Event('change', { bubbles: true }));
  });
}
function body() {
  const call = api.mock.calls.at(-1)!;
  expect(call[0]).toBe('/api/heartbeat/settings');
  expect(call[1].method).toBe('PUT');
  return JSON.parse(call[1].body);
}

it('saves interval, quiet hours and gap in one PUT', async () => {
  await render();
  await change(container.querySelector('select')!, '1440');
  const times = container.querySelectorAll<HTMLInputElement>('input[type="time"]');
  await change(times[0], '22:00');
  await change(times[1], '07:00');
  await click('Save');
  expect(body()).toEqual({ interval_minutes: 1440, quiet_hours: '22:00-07:00', min_gap_hours: 4 });
  expect(success).toHaveBeenCalledWith('Check-in settings saved.');
});

it('sends quiet_hours off when both times are empty, and nothing when only one is set', async () => {
  await render();
  await click('Save');
  expect(body().quiet_hours).toBe('off');
  await change(container.querySelector('input[type="time"]')!, '22:00');
  const previous = api.mock.calls.length;
  await click('Save');
  expect(api.mock.calls).toHaveLength(previous);
  expect(error).toHaveBeenCalledWith('Set both a start and an end for quiet hours, or clear both.');
  await click('Clear');
  await click('Save');
  expect(body().quiet_hours).toBe('off');
});

it('Resume sends pause_days 0', async () => {
  hb.paused_until = '2026-12-01T12:00:00';
  await render();
  expect(container.textContent).toContain('Paused until');
  await click('Resume');
  expect(body()).toEqual({ pause_days: 0 });
});

it('keeps a non-preset interval and gap that Baker set', async () => {
  hb.interval_minutes = 90;
  hb.min_gap_hours = 3;
  await render();
  const selects = container.querySelectorAll('select');
  expect(selects[0].value).toBe('90');
  expect(selects[1].value).toBe('3');
  await click('Save');
  // An unchanged interval is not resent, so it is never re-validated.
  expect(body()).toEqual({ quiet_hours: 'off', min_gap_hours: 3 });
});

it('an operator default outside 30-1440 does not block saving quiet hours', async () => {
  hb.interval_minutes = 15;
  await render();
  expect(container.querySelectorAll('select')[0].value).toBe('15');
  const [start, end] = container.querySelectorAll<HTMLInputElement>('input[type="time"]');
  await change(start, '22:00');
  await change(end, '07:00');
  await click('Save');
  expect(body()).toEqual({ quiet_hours: '22:00-07:00', min_gap_hours: 4 });
});

it('toggles only the check-in and applies the returned settings', async () => {
  await render();
  const buttons = [...container.querySelectorAll('button')].filter(b => b.textContent === 'Turn off');
  await act(async () => { buttons.at(-1)!.click(); });
  expect(body()).toEqual({ enabled: false });
  expect(container.textContent).toContain('Turn on');
});

it('shows validation errors and releases the save button after a failed request', async () => {
  await render();
  api.mockRejectedValueOnce(new ApiError('Invalid quiet hours', 400, 'Invalid quiet hours'));
  await click('Save');
  expect(error).toHaveBeenCalledWith('Invalid quiet hours');
  expect([...container.querySelectorAll('button')].find(b => b.textContent === 'Save')!.disabled).toBe(false);
});
