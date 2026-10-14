// @vitest-environment jsdom
//
// #293: the member card that signs a seat's own devices in to the no-login todo app.
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

const { TodoDevicesCard } = await import('./TodoDevicesCard');

const LINK = {
  url: 'http://localhost/todo/signin/abc',
  qr: 'data:image/png;base64,AAAA',
  expires_at: '2026-10-14T05:00:00+00:00',
};

let container: HTMLDivElement;
let root: Root;

/** Answer each route the card calls; record nothing else. */
function serve(devices: { enabled: boolean; count: number }) {
  api.mockImplementation((url: string) => {
    if (url === '/api/crm/gtd/web-sessions') return Promise.resolve(devices);
    if (url === '/api/crm/gtd/web-signin-link') return Promise.resolve(LINK);
    if (url === '/api/crm/gtd/web-signout-all') return Promise.resolve({ ok: true });
    return Promise.reject(new Error(`unexpected ${url}`));
  });
}

async function render() {
  await act(async () => root.render(<TodoDevicesCard isMobile={false} />));
}

function button(label: string): HTMLButtonElement | undefined {
  return [...container.querySelectorAll('button')].find(b => b.textContent === label) as HTMLButtonElement | undefined;
}

async function click(el: HTMLElement) {
  await act(async () => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
}

beforeEach(() => {
  api.mockReset();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe('TodoDevicesCard', () => {
  it('says the app is off and offers nothing while it is switched off', async () => {
    serve({ enabled: false, count: 0 });
    await render();
    expect(container.textContent).toContain('switched off');
    expect(container.querySelectorAll('button')).toHaveLength(0);
  });

  it('shows how many devices are signed in', async () => {
    serve({ enabled: true, count: 2 });
    await render();
    expect(container.textContent).toContain('2 devices signed in.');
  });

  it('shows a one-time code as a QR and a link', async () => {
    serve({ enabled: true, count: 0 });
    await render();
    await click(button('Show a sign-in code')!);
    expect(api).toHaveBeenCalledWith('/api/crm/gtd/web-signin-link', { method: 'POST' });
    const img = container.querySelector('img[alt="Sign-in code"]') as HTMLImageElement;
    expect(img.getAttribute('src')).toBe(LINK.qr);
    expect(container.textContent).toContain(LINK.url);
    expect(button('Make a new code')).toBeDefined();
  });

  it('signs out everywhere only after confirming, and resets the count', async () => {
    serve({ enabled: true, count: 3 });
    await render();
    vi.stubGlobal('confirm', () => false);
    await click(button('Sign out everywhere')!);
    expect(api).not.toHaveBeenCalledWith('/api/crm/gtd/web-signout-all', expect.anything());

    vi.stubGlobal('confirm', () => true);
    await click(button('Sign out everywhere')!);
    expect(api).toHaveBeenCalledWith('/api/crm/gtd/web-signout-all', { method: 'POST' });
    expect(container.textContent).toContain('No devices signed in yet.');
  });
});
