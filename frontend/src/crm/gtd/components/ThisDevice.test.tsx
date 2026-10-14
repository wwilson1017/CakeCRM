// @vitest-environment jsdom
//
// #293: the phone app's own Sign out — this device only, after a confirm.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const signOut = vi.hoisted(() => vi.fn());
vi.mock('../api', () => ({ signOut }));

const { ThisDevice } = await import('./ThisDevice');

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  signOut.mockReset();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

async function press(onSignedOut: () => void) {
  await act(async () => root.render(<ThisDevice onSignedOut={onSignedOut} />));
  const button = container.querySelector('button')!;
  await act(async () => { button.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
}

describe('ThisDevice', () => {
  it('does nothing when the confirm is cancelled', async () => {
    vi.stubGlobal('confirm', () => false);
    const done = vi.fn();
    await press(done);
    expect(signOut).not.toHaveBeenCalled();
    expect(done).not.toHaveBeenCalled();
  });

  it('signs this device out, then hands over', async () => {
    vi.stubGlobal('confirm', () => true);
    signOut.mockResolvedValue({ ok: true });
    const done = vi.fn();
    await press(done);
    expect(signOut).toHaveBeenCalledOnce();
    expect(done).toHaveBeenCalledOnce();
  });

  it('says so when the sign-out fails, and stays put', async () => {
    vi.stubGlobal('confirm', () => true);
    signOut.mockRejectedValue(new Error('offline'));
    const done = vi.fn();
    await press(done);
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("Couldn't sign out");
    expect(done).not.toHaveBeenCalled();
  });
});
