// @vitest-environment jsdom
//
// The "share this mailbox with all seats" control (#194).
//
// There is one Gmail connection per install, so this checkbox decides whether every seat
// or only admins are offered the Gmail tools. What is pinned here is the part a wrong
// render would make dangerous: the box reflects the SERVER's value rather than a local
// guess, a click sends the new value to the admin-only route, and a failed write leaves
// the box showing what the server actually holds — never a UI that says "shared" while
// the backend says otherwise.
//
// The card renders under a Router (it reads the OAuth callback's query params) and calls
// `api` for its status, so both are mocked; `SettingsCard` and the shared style modules
// are real.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => vi.fn());
vi.mock('../../core/api/client', () => ({ api }));

const CONNECTED = {
  connected: true,
  email: 'ops@example.com',
  connection_status: 'ok',
  client_id: 'cid.apps.googleusercontent.com',
  client_secret_present: true,
  scopes: ['gmail.readonly', 'gmail.compose'],
  share_with_all_seats: false,
  redirect_uri: 'https://example.test/api/gmail/oauth/callback',
};

let container: HTMLDivElement;
let root: Root;

async function renderCard() {
  const { GmailCard } = await import('./GmailCard');
  await act(async () => {
    root.render(
      <MemoryRouter>
        <GmailCard isMobile={false} />
      </MemoryRouter>,
    );
  });
}

const checkbox = () => container.querySelector<HTMLInputElement>('input[type="checkbox"]');

/** A real click: the browser flips `checked` and React's synthetic onChange fires from
 *  it, which is also what restores a controlled checkbox when state does not move. */
async function toggle() {
  await act(async () => {
    checkbox()!.click();
  });
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
  vi.resetModules();
});

describe('Gmail mailbox sharing', () => {
  it('reflects the install policy the server reports', async () => {
    api.mockResolvedValue({ ...CONNECTED, share_with_all_seats: true });
    await renderCard();
    expect(checkbox()?.checked).toBe(true);
  });

  it('is off by default, which is what keeps a member out of the admin’s mailbox', async () => {
    api.mockResolvedValue(CONNECTED);
    await renderCard();
    expect(checkbox()?.checked).toBe(false);
    expect(container.textContent).toContain('only admins can ask the assistant');
  });

  it('PUTs the new value to the sharing route and adopts the response', async () => {
    api.mockResolvedValueOnce(CONNECTED);                                   // initial status
    api.mockResolvedValueOnce({ ...CONNECTED, share_with_all_seats: true }); // the PUT
    await renderCard();
    await toggle();

    const put = api.mock.calls.find(([path]) => path === '/api/gmail/sharing');
    expect(put, 'the toggle must write through the admin-only route').toBeTruthy();
    expect(put![1]).toMatchObject({ method: 'PUT' });
    expect(JSON.parse((put![1] as RequestInit).body as string)).toEqual({ shared: true });
    expect(checkbox()?.checked).toBe(true);
  });

  it('a failed write leaves the box showing the server’s value', async () => {
    api.mockResolvedValueOnce(CONNECTED);              // initial status
    api.mockRejectedValueOnce(new Error('boom'));      // the PUT fails
    api.mockResolvedValueOnce(CONNECTED);              // the refresh that follows
    await renderCard();
    await toggle();

    // Never a UI that claims the mailbox is shared when the backend says it is not.
    expect(checkbox()?.checked).toBe(false);
  });

  it('is not offered before a mailbox is connected', async () => {
    api.mockResolvedValue({ ...CONNECTED, connected: false, email: '' });
    await renderCard();
    expect(checkbox()).toBeNull();
  });
});
