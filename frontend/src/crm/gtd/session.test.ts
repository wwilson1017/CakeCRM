import { afterEach, describe, expect, it, vi } from 'vitest';
import { resetSession, sessionState, setSession, signedOut } from './session';

// The no-login app's session store (#293): state and subscriptions only.
afterEach(() => resetSession());

describe('session store', () => {
  it('starts loading and moves on set', () => {
    expect(sessionState()).toBe('loading');
    setSession('ok');
    expect(sessionState()).toBe('ok');
  });

  it('a 401 anywhere flips it to signed out', () => {
    setSession('ok');
    signedOut();
    expect(sessionState()).toBe('signedOut');
  });

  it('reset returns to loading so the gate asks again', () => {
    setSession('error');
    resetSession();
    expect(sessionState()).toBe('loading');
  });
});

describe('rawTodoFetch on a 401', () => {
  it('marks the session signed out and throws an ApiError carrying the status', async () => {
    vi.resetModules();
    vi.stubGlobal('window', { location: { pathname: '/todo' } });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Not signed in' }), { status: 401 }),
    ));
    const api = await import('./api');
    const session = await import('./session');
    const { ApiError } = await import('../../core/api/client');
    session.setSession('ok');
    const err = await api.getSession().catch(e => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(401);
    expect(session.sessionState()).toBe('signedOut');
    vi.unstubAllGlobals();
  });
});
