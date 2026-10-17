// @vitest-environment jsdom
//
// #322: the OAuth consent page that ChatGPT or Claude opens in its own window.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { ConsentPage } = await import('./ConsentPage');
const { leave } = await import('./leave');

const INFO = {
  client_name: 'ChatGPT', redirect_host: 'chatgpt.com', loopback: false,
  write_requested: true, email: 'member@example.com',
};

let container: HTMLDivElement;
let root: Root;
let calls: { url: string; init: RequestInit }[];

type Reply = { status: number; body: unknown };

function serve(routes: Record<string, Reply | ((init: RequestInit) => Reply)>) {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    const key = `${init.method} ${url}`;
    const route = routes[key];
    if (!route) throw new Error(`unexpected ${key}`);
    const r = typeof route === 'function' ? route(init) : route;
    return { status: r.status, json: async () => r.body } as Response;
  }));
}

function type(id: string, value: string) {
  const el = container.querySelector(id) as HTMLInputElement;
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

async function submit() {
  await act(async () => {
    container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
}

async function click(label: string) {
  const b = [...container.querySelectorAll('button')].find(x => x.textContent === label)!;
  await act(async () => { b.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
}

async function signIn() {
  await act(async () => root.render(<ConsentPage />));
  await act(async () => { type('#consent-email', 'member@example.com'); type('#consent-password', 'pw'); });
  await submit();
}

beforeEach(() => {
  calls = [];
  window.history.replaceState(null, '', '/oauth/consent?request=rid1');
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  leave.to = vi.fn();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe('ConsentPage', () => {
  it('signs in without touching this tab\'s session or broadcasting to other tabs', async () => {
    const channel = vi.fn();
    vi.stubGlobal('BroadcastChannel', channel);
    serve({
      'POST /api/login': { status: 200, body: { access_token: 'jwt-1' } },
      'GET /api/mcp/consent/rid1': { status: 200, body: INFO },
    });
    await signIn();
    expect(sessionStorage.length).toBe(0);
    expect(channel).not.toHaveBeenCalled();
    expect((calls[1].init.headers as Record<string, string>).Authorization).toBe('Bearer jwt-1');
  });

  it('names the app and the redirect host, and posts a read-only choice', async () => {
    let posted: unknown;
    serve({
      'POST /api/login': { status: 200, body: { access_token: 'jwt-1' } },
      'GET /api/mcp/consent/rid1': { status: 200, body: INFO },
      'POST /api/mcp/consent/rid1': (init) => {
        posted = JSON.parse(init.body as string);
        return { status: 200, body: { redirect: 'https://chatgpt.com/cb?code=c' } };
      },
    });
    await signIn();
    expect(container.textContent).toContain('ChatGPT wants to work in CakeCRM as member@example.com');
    expect(container.textContent).toContain('chatgpt.com');
    const readOnly = container.querySelectorAll('input[type=radio]')[1] as HTMLInputElement;
    await act(async () => { readOnly.click(); });
    await click('Allow');
    expect(posted).toEqual({ approve: true, read_only: true });
    expect(leave.to).toHaveBeenCalledWith('https://chatgpt.com/cb?code=c');
  });

  it('asks for the 2FA code when the account has one', async () => {
    serve({
      'POST /api/login': { status: 200, body: { requires_2fa: true, pending_token: 'p' } },
      'POST /api/login/verify-2fa': { status: 200, body: { access_token: 'jwt-2' } },
      'GET /api/mcp/consent/rid1': { status: 200, body: INFO },
    });
    await signIn();
    expect(container.textContent).toContain('6-digit code');
    await act(async () => { type('input[aria-label="Verification code"]', '123456'); });
    await submit();
    expect(JSON.parse(calls[1].init.body as string)).toEqual({ pending_token: 'p', code: '123456', trust_device: false });
    expect(container.textContent).toContain('wants to work in CakeCRM');
  });

  it('says the request expired when the server no longer has it', async () => {
    serve({
      'POST /api/login': { status: 200, body: { access_token: 'jwt-1' } },
      'GET /api/mcp/consent/rid1': { status: 404, body: { detail: 'expired' } },
    });
    await signIn();
    expect(container.textContent).toContain('This sign-in request has expired');
  });

  it('offers only read access when the app asked only to read', async () => {
    serve({
      'POST /api/login': { status: 200, body: { access_token: 'jwt-1' } },
      'GET /api/mcp/consent/rid1': { status: 200, body: { ...INFO, write_requested: false } },
    });
    await signIn();
    const [rw, ro] = container.querySelectorAll('input[type=radio]') as NodeListOf<HTMLInputElement>;
    expect(rw.disabled).toBe(true);
    expect(ro.checked).toBe(true);
  });
});
