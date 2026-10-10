// @vitest-environment jsdom
//
// Sign-out wipes the CRM warm cache (#281) — both this tab's own logout and the cross-tab one,
// because rows cached in this browser must not outlive the session that fetched them.
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const wipeWarm = vi.hoisted(() => vi.fn(async () => {}));
vi.mock('../../crm/warmStore', () => ({ wipeWarm }));

/** Every channel the provider opens, so a test can deliver another tab's message. */
const channels: FakeChannel[] = [];
class FakeChannel {
  onmessage: ((e: MessageEvent) => void) | null = null;
  constructor() { channels.push(this); }
  postMessage() {}
  close() {}
}

const { AuthProvider, useAuth } = await import('./AuthContext');

let logout: (() => void) | null = null;
function Grab() {
  const auth = useAuth();
  useEffect(() => { logout = auth.logout; });
  return null;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(async () => {
  vi.stubGlobal('BroadcastChannel', FakeChannel);
  channels.length = 0;
  wipeWarm.mockClear();
  sessionStorage.clear();
  container = document.createElement('div');
  root = createRoot(container);
  await act(async () => { root.render(<AuthProvider><Grab /></AuthProvider>); });
});

afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
});

describe('sign-out wipes the warm cache (#281)', () => {
  it("on this tab's logout", () => {
    act(() => logout!());
    expect(wipeWarm).toHaveBeenCalledTimes(1);
  });

  it("on another tab's logout", async () => {
    const listener = channels.find(c => c.onmessage !== null);
    await act(async () => { listener!.onmessage!({ data: { type: 'logout' } } as MessageEvent); });
    expect(wipeWarm).toHaveBeenCalledTimes(1);
  });
});
