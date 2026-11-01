// @vitest-environment jsdom
//
// #322: the admin card — the members switch and every seat's AI tool connections.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => vi.fn());
vi.mock('../../core/api/client', () => ({ api, ApiError: Error }));
vi.mock('../../shared/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const { AiToolConnectionsCard } = await import('./AiToolConnectionsCard');

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  api.mockReset();
  api.mockImplementation((url: string, init?: RequestInit) => {
    if (url === '/api/mcp/admin/connections') {
      return Promise.resolve({ members_enabled: false, connections: [{
        id: 3, kind: 'token', name: 'Codex', read_only: false, user_name: 'Sam Seat',
        created_at: '2026-10-01T10:00:00+00:00', last_used_at: '2026-10-02T10:00:00+00:00',
        expires_at: '2026-12-30T10:00:00+00:00',
      }] });
    }
    if (url === '/api/mcp/admin/settings') return Promise.resolve(JSON.parse(init!.body as string));
    if (url === '/api/mcp/admin/connections/3') return Promise.resolve({ ok: true });
    return Promise.reject(new Error(`unexpected ${url}`));
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe('AiToolConnectionsCard', () => {
  it('shows every seat\'s connections and turns the members switch on', async () => {
    await act(async () => root.render(<AiToolConnectionsCard isMobile={false} />));
    expect(container.textContent).toContain('Sam Seat');
    expect(container.textContent).toContain('Codex');
    const box = container.querySelector('input[type=checkbox]') as HTMLInputElement;
    expect(box.checked).toBe(false);
    await act(async () => { box.click(); });
    expect(api).toHaveBeenCalledWith('/api/mcp/admin/settings', { method: 'PUT', body: '{"members_enabled":true}' });
    expect(box.checked).toBe(true);
  });

  it('revokes through the admin route', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    await act(async () => root.render(<AiToolConnectionsCard isMobile={false} />));
    const revoke = [...container.querySelectorAll('button')].find(b => b.textContent === 'Revoke')!;
    await act(async () => { revoke.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    expect(api).toHaveBeenCalledWith('/api/mcp/admin/connections/3', { method: 'DELETE' });
  });
});
