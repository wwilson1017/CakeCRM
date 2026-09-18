// @vitest-environment jsdom
//
// The personal half of the Telegram split (#193, multi-user Phase B / B4).
//
// The card is member-visible and shows a link code with no redaction, which is only
// safe because a per-seat code claims the CALLER's own row — so the contracts worth
// pinning are about which state renders which affordance, and about the two writes
// going to the per-user routes rather than the retired install-wide one:
//
//  • no bot connected → an explanatory line and NO way to mint a code;
//  • connected, no code yet → a "Get my link code" button, and a GET that never mints;
//  • a code minted → the deep link, the /link fallback and Regenerate;
//  • linked → who it is linked to, and Unlink.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => vi.fn());
vi.mock('../../core/api/client', () => ({ api }));
vi.mock('../../shared/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const { TelegramLinkCard } = await import('./TelegramLinkCard');

const DISCONNECTED = {
  connected: false, bot_username: '', linked: false,
  linked_name: '', link_code: '', link_url: '',
};
const NO_CODE = { ...DISCONNECTED, connected: true, bot_username: 'acmebot' };
const WITH_CODE = {
  ...NO_CODE, link_code: 'CODE123', link_url: 'https://t.me/acmebot?start=CODE123',
};
const LINKED = { ...NO_CODE, linked: true, linked_name: 'Alex' };

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.useFakeTimers();
  api.mockReset();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

async function render(status: Record<string, unknown>) {
  api.mockResolvedValue(status);
  await act(async () => root.render(<TelegramLinkCard isMobile={false} />));
}

function buttonLabelled(label: string): HTMLButtonElement | undefined {
  return [...container.querySelectorAll('button')].find(
    b => (b.textContent ?? '').trim() === label,
  ) as HTMLButtonElement | undefined;
}

function click(label: string) {
  const button = buttonLabelled(label);
  if (!button) throw new Error(`no button labelled "${label}"`);
  return act(async () => { button.click(); });
}

describe('TelegramLinkCard — which state offers which affordance', () => {
  it('explains rather than offers when no bot is connected', async () => {
    await render(DISCONNECTED);
    expect(container.textContent).toContain('No Telegram bot is connected');
    // Nothing here can work yet, so nothing is offered.
    expect(buttonLabelled('Get my link code')).toBeUndefined();
    expect(buttonLabelled('Unlink my Telegram')).toBeUndefined();
  });

  it('offers a code to mint once a bot is connected, and shows none before that', async () => {
    await render(NO_CODE);
    expect(buttonLabelled('Get my link code')).toBeDefined();
    expect(container.textContent).not.toContain('/link');
    expect(container.querySelector('a')).toBeNull();
  });

  it('never mints a code just by rendering — a GET has no side effect', async () => {
    await render(NO_CODE);
    expect(api).toHaveBeenCalledTimes(1);
    expect(api.mock.calls[0][0]).toBe('/api/telegram/status');
    expect(api.mock.calls[0][1]).toBeUndefined();     // a plain GET
  });

  it('mints through the per-user route, not the retired install-wide one', async () => {
    await render(NO_CODE);
    api.mockResolvedValue(WITH_CODE);
    await click('Get my link code');
    expect(api).toHaveBeenLastCalledWith('/api/telegram/link-code', { method: 'POST' });
    // The route a per-seat code REPLACED claimed the one install-wide binding.
    const paths = api.mock.calls.map(c => c[0]);
    expect(paths).not.toContain('/api/telegram/link-code/regenerate');
  });

  it('shows the deep link, the code and Regenerate once a code exists', async () => {
    await render(WITH_CODE);
    const link = container.querySelector('a');
    expect(link?.getAttribute('href')).toBe('https://t.me/acmebot?start=CODE123');
    expect(container.textContent).toContain('/link CODE123');
    expect(buttonLabelled('Regenerate code')).toBeDefined();
  });

  it('says the code is the caller\'s own, which is why it is not redacted', async () => {
    await render(WITH_CODE);
    expect(container.textContent).toContain('It is yours alone');
  });

  it('names who the chat is linked to and offers Unlink', async () => {
    await render(LINKED);
    expect(container.textContent).toContain('Linked as Alex');
    expect(container.textContent).toContain('@acmebot');
    expect(buttonLabelled('Unlink my Telegram')).toBeDefined();
    // Nothing to mint while linked — regenerating is how you move devices.
    expect(buttonLabelled('Get my link code')).toBeUndefined();
  });

  it('unlinks through the self-service route', async () => {
    await render(LINKED);
    api.mockResolvedValue(NO_CODE);
    await click('Unlink my Telegram');
    expect(api).toHaveBeenLastCalledWith('/api/telegram/unlink', { method: 'POST' });
    expect(buttonLabelled('Unlink my Telegram')).toBeUndefined();
  });
});

describe('TelegramLinkCard — the waiting poll', () => {
  it('polls only while an unused code is outstanding', async () => {
    await render(WITH_CODE);
    expect(api).toHaveBeenCalledTimes(1);
    await act(async () => { vi.advanceTimersByTime(4000); });
    expect(api).toHaveBeenCalledTimes(2);
  });

  it('does not poll before a code has been minted', async () => {
    await render(NO_CODE);
    await act(async () => { vi.advanceTimersByTime(12000); });
    expect(api).toHaveBeenCalledTimes(1);
  });

  it('stops polling once the chat is linked', async () => {
    await render(LINKED);
    await act(async () => { vi.advanceTimersByTime(12000); });
    expect(api).toHaveBeenCalledTimes(1);
  });
});
