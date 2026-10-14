// @vitest-environment jsdom
//
// #293: the sign-in page spends its code ONLY on the button's press. Link previews and
// scanners load links, so a load that redeemed would burn the code before the phone did.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../core/api/client';

const getSession = vi.hoisted(() => vi.fn());
const redeemSignIn = vi.hoisted(() => vi.fn());
vi.mock('./api', () => ({ getSession, redeemSignIn }));

const { SignInPage } = await import('./SignInPage');

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  getSession.mockReset().mockRejectedValue(new ApiError('API error 401: Not signed in', 401, 'Not signed in'));
  redeemSignIn.mockReset();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render(onSignedIn = vi.fn()) {
  await act(async () => root.render(
    <MemoryRouter initialEntries={['/signin/tok']}>
      <Routes><Route path="/signin/:token" element={<SignInPage onSignedIn={onSignedIn} />} /></Routes>
    </MemoryRouter>,
  ));
  return onSignedIn;
}

function signInButton(): HTMLButtonElement {
  return [...container.querySelectorAll('button')].find(b => b.textContent === 'Open my list on this device') as HTMLButtonElement;
}

async function press() {
  await act(async () => { signInButton().dispatchEvent(new MouseEvent('click', { bubbles: true })); });
}

describe('SignInPage', () => {
  it('loading the page redeems nothing', async () => {
    await render();
    expect(signInButton()).toBeDefined();
    expect(redeemSignIn).not.toHaveBeenCalled();
  });

  it('the button spends the code and hands over to the app', async () => {
    redeemSignIn.mockResolvedValue({ ok: true });
    const onSignedIn = await render();
    await press();
    expect(redeemSignIn).toHaveBeenCalledWith('tok');
    expect(onSignedIn).toHaveBeenCalledOnce();
  });

  it('a used, expired or made-up code says how to get a new one', async () => {
    redeemSignIn.mockRejectedValue(new ApiError('API error 404: Not found', 404, 'Not found'));
    const onSignedIn = await render();
    await press();
    expect(container.textContent).toContain("isn't valid any more");
    expect(onSignedIn).not.toHaveBeenCalled();
  });

  it('notes when this browser is already signed in', async () => {
    getSession.mockResolvedValue({ ok: true });
    await render();
    expect(container.textContent).toContain('already signed in');
  });
});
