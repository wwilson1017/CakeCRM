// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { InstallAppCard } from './InstallAppCard';

const installWindow = window as Window & { __installPrompt?: Event & { prompt(): Promise<void> } | null };
let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Desktop');
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  delete installWindow.__installPrompt;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
async function render(isMobile = false) {
  await act(async () => root.render(<InstallAppCard isMobile={isMobile} />));
}
const openLabels = () => [...host.querySelectorAll('details[open] summary')].map(el => el.textContent);
const button = () => host.querySelector('button');
function nativePrompt() {
  return Object.assign(new Event('beforeinstallprompt', { cancelable: true }), {
    prompt: vi.fn<() => Promise<void>>().mockResolvedValue(undefined),
  });
}

describe('InstallAppCard', () => {
  it.each([
    ['iPhone', 'iPhone or iPad (Safari)'],
    ['Android', 'Android (Chrome)'],
    ['Desktop', 'Computer (Chrome or Edge)'],
  ])('opens the matching steps for %s', async (ua, label) => {
    vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue(ua);
    await render(ua !== 'Desktop');
    expect(openLabels()).toEqual([label]);
    expect(host.querySelectorAll('details')).toHaveLength(3);
    expect(host.textContent).toContain('Open as Web App');
    expect(host.textContent).toContain('Install and create shortcut');
    if (ua === 'Desktop') expect(host.textContent).toContain(location.origin);
  });

  it('recognises an iPad identifying as a Mac', async () => {
    vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Macintosh');
    vi.stubGlobal('navigator', { userAgent: 'Macintosh', maxTouchPoints: 5 });
    await render();
    expect(openLabels()).toEqual(['iPhone or iPad (Safari)']);
  });

  it('offers no native button without a held event', async () => {
    await render();
    expect(button()).toBeNull();
  });

  it.each([false, true])('consumes a stashed prompt even when it rejects (%s)', async rejects => {
    const event = nativePrompt();
    if (rejects) event.prompt.mockRejectedValue(new Error('unavailable'));
    installWindow.__installPrompt = event;
    await render();
    expect(button()?.textContent).toBe('Install CakeCRM');
    await act(async () => { button()!.click(); });
    expect(event.prompt).toHaveBeenCalledTimes(1);
    expect(button()).toBeNull();
    expect(installWindow.__installPrompt).toBeNull();
  });

  it('captures a late event and clears it when the browser installs the app', async () => {
    await render();
    const event = nativePrompt();
    act(() => { window.dispatchEvent(event); });
    expect(event.defaultPrevented).toBe(true);
    expect(button()).not.toBeNull();
    act(() => { window.dispatchEvent(new Event('appinstalled')); });
    expect(button()).toBeNull();
    expect(installWindow.__installPrompt).toBeNull();
    expect(host.textContent).toContain("You're using the installed app");
  });

  it.each(['media', 'ios'])('recognises an installed app through %s', async mode => {
    installWindow.__installPrompt = nativePrompt();
    if (mode === 'media') {
      vi.stubGlobal('matchMedia', (query: string) => ({ matches: query === '(display-mode: standalone)' }));
    } else {
      vi.stubGlobal('navigator', { userAgent: 'iPhone', standalone: true });
    }
    await render();
    expect(host.textContent).toContain("You're using the installed app");
    expect(button()).toBeNull();
  });

  it('the shell holds an early prompt and clears it on installation before Settings loads', () => {
    const shells = import.meta.glob('../../../index.html', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;
    const shell = shells['../../../index.html'];
    const script = shell.match(/<script>([\s\S]*?)<\/script>/)![1];
    const boot = (pathname: string) => {
      const target = new EventTarget() as EventTarget & { __installPrompt?: unknown };
      // Execute the real boot listener against an isolated window; the theme script needs matchMedia.
      Object.assign(target, { matchMedia: () => ({ matches: false }), location: { pathname } });
      new Function('window', 'document', 'localStorage', script)(target, document, { getItem: () => null });
      return target;
    };
    const target = boot('/crm/todos');
    const event = nativePrompt();
    target.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    expect(target.__installPrompt).toBe(event);
    target.dispatchEvent(new Event('appinstalled'));
    expect(target.__installPrompt).toBeNull();
    // The no-login todo app has no Install card, so Chrome keeps its own banner there.
    for (const path of ['/todo', '/todo/inbox']) {
      const todo = boot(path);
      const held = nativePrompt();
      todo.dispatchEvent(held);
      expect(held.defaultPrevented).toBe(false);
      expect(todo.__installPrompt).toBeUndefined();
    }
  });
});
