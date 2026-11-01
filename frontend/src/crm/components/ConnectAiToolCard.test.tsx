// @vitest-environment jsdom
//
// #322: the member card that connects a person's own AI tool to CakeCRM.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => vi.fn());
vi.mock('../../core/api/client', () => {
  class ApiError extends Error {
    status: number;
    detail: string;
    constructor(message: string, status: number, detail: string) {
      super(message);
      this.status = status;
      this.detail = detail;
    }
  }
  return { api, ApiError };
});
vi.mock('../../shared/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const { ConnectAiToolCard } = await import('./ConnectAiToolCard');

const CONN = {
  id: 7, kind: 'oauth', name: 'ChatGPT', read_only: true,
  created_at: '2026-10-01T10:00:00+00:00', last_used_at: '2026-10-16T09:00:00+00:00', expires_at: null,
};

let container: HTMLDivElement;
let root: Root;
let posted: unknown;

function serve(mine: object) {
  api.mockImplementation((url: string, init?: RequestInit) => {
    if (url === '/api/mcp/connections') return Promise.resolve(mine);
    if (url === '/api/mcp/tokens') { posted = JSON.parse(init!.body as string); return Promise.resolve({ token: 'tok-123' }); }
    if (url === '/api/mcp/connections/7' && init?.method === 'DELETE') return Promise.resolve({ ok: true });
    if (url === '/api/mcp/connections/7/calls') {
      return Promise.resolve({ calls: [{ id: 1, tool: 'crm_find_contact', detail: '3 rows', ok: true, refused: null, duration_ms: 12, at: '2026-10-16T09:00:00+00:00' }] });
    }
    return Promise.reject(new Error(`unexpected ${url}`));
  });
}

async function render() {
  await act(async () => root.render(<ConnectAiToolCard isMobile={false} />));
}

function button(label: string) {
  return [...container.querySelectorAll('button')].find(b => b.textContent === label) as HTMLButtonElement | undefined;
}

async function click(el: HTMLElement) {
  await act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
}

function type(selector: string, value: string) {
  const el = container.querySelector(selector) as HTMLInputElement;
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

beforeEach(() => {
  api.mockReset();
  posted = undefined;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe('ConnectAiToolCard', () => {
  it('says an admin has not switched it on, and offers no control', async () => {
    serve({ may_connect: false, oauth_url: null, connections: [] });
    await render();
    expect(container.textContent).toContain('An admin has not switched on');
    expect(container.querySelectorAll('button, input, select')).toHaveLength(0);
  });

  it('lists connections with kind, read-only and the call log', async () => {
    serve({ may_connect: true, oauth_url: null, connections: [CONN] });
    await render();
    expect(container.textContent).toContain('ChatGPT');
    expect(container.textContent).toContain('Signed in');
    expect(container.textContent).toContain('Read only');
    await click(button('Calls')!);
    expect(container.textContent).toContain('crm_find_contact');
    expect(container.textContent).toContain('3 rows');
  });

  it('creates a token, shows it once, and fills it into the Claude Code command', async () => {
    serve({ may_connect: true, oauth_url: null, connections: [] });
    await render();
    await act(async () => { type('input[aria-label="Token name"]', 'Laptop'); });
    await click(button('Create token')!);
    expect(posted).toEqual({ name: 'Laptop', read_only: false, lifetime_days: 90 });
    expect(container.textContent).toContain('It is not shown again');
    expect(container.textContent).toContain(
      `claude mcp add --transport http cakecrm ${window.location.origin}/mcp --header "Authorization: Bearer tok-123"`,
    );
  });

  it('revokes a connection after confirming', async () => {
    serve({ may_connect: true, oauth_url: null, connections: [CONN] });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    await render();
    await click(button('Revoke')!);
    expect(api).toHaveBeenCalledWith('/api/mcp/connections/7', { method: 'DELETE' });
    expect(container.textContent).toContain('No AI tools are connected');
  });

  it('shows the ChatGPT and Claude address only when the install has a public one', async () => {
    serve({ may_connect: true, oauth_url: null, connections: [] });
    await render();
    expect(container.textContent).toContain('needs this install to have a public https');
    act(() => root.unmount());
    root = createRoot(container);
    serve({ may_connect: true, oauth_url: 'https://crm.example/mcp', connections: [] });
    await render();
    expect(container.textContent).toContain('https://crm.example/mcp');
    expect(container.textContent).not.toContain('needs this install');
  });
});
