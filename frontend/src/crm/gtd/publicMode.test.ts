// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * publicMode resolves its constants at module load from `window`, so each case has to
 * set the environment and then re-import through a reset module registry. That is also
 * the honest shape of the thing under test: the basename is decided exactly once, when
 * the bundle loads on whatever URL the backend served.
 */
async function loadWith({ injected, pathname }: { injected?: string; pathname: string }) {
  vi.resetModules();
  window.history.replaceState({}, '', pathname);
  const w = window as { __CAKECRM_TODO_BASE__?: string };
  if (injected === undefined) delete w.__CAKECRM_TODO_BASE__;
  else w.__CAKECRM_TODO_BASE__ = injected;
  return import('./publicMode');
}

const originalPath = '/';

beforeEach(() => { window.history.replaceState({}, '', originalPath); });
afterEach(() => {
  delete (window as { __CAKECRM_TODO_BASE__?: string }).__CAKECRM_TODO_BASE__;
});

describe('inside the CRM', () => {
  it('is not public mode and targets the authed API', async () => {
    const m = await loadWith({ pathname: '/crm/todos' });
    expect(m.isTodoPublicMode).toBe(false);
    expect(m.TODO_PUBLIC_BASE).toBeNull();
    expect(m.TODO_API_BASE).toBe('/api/crm/gtd');
    expect(m.todoPath('/inbox')).toBe('/crm/todos/inbox');
    expect(m.todoPath()).toBe('/crm/todos');
  });
});

describe('served by the backend at /todo', () => {
  it('adopts the injected tokenless base', async () => {
    const m = await loadWith({ injected: '/todo', pathname: '/todo/inbox' });
    expect(m.isTodoPublicMode).toBe(true);
    expect(m.TODO_API_BASE).toBe('/api/todo-web');
    expect(m.todoPath('/inbox')).toBe('/inbox');
    // The root page hangs off '/', because the router basename is already the app root.
    expect(m.todoPath()).toBe('/');
  });

  it('carries the secret token into the API base', async () => {
    const m = await loadWith({ injected: '/todo/s3cret', pathname: '/todo/s3cret/next' });
    expect(m.TODO_API_BASE).toBe('/api/todo-web/s3cret');
  });

  it('trusts the injected value over the URL', async () => {
    // The backend is authoritative: it matched the token and told us the base. A URL
    // the SPA merely happens to be on must never override that.
    const m = await loadWith({ injected: '/todo/s3cret', pathname: '/todo/s3cret/projects/4' });
    expect(m.TODO_PUBLIC_BASE).toBe('/todo/s3cret');
  });
});

describe('vite dev server (nothing injected)', () => {
  it('recovers a token from the URL', async () => {
    const m = await loadWith({ pathname: '/todo/s3cret/inbox' });
    expect(m.TODO_PUBLIC_BASE).toBe('/todo/s3cret');
    expect(m.TODO_API_BASE).toBe('/api/todo-web/s3cret');
  });

  it('does not mistake a page slug for a token', async () => {
    // The backend refuses to store any of these as a token (RESERVED_TODO_WEB_SLUGS),
    // which is exactly what makes this fallback unambiguous.
    const m = await loadWith({ pathname: '/todo/inbox' });
    expect(m.TODO_PUBLIC_BASE).toBe('/todo');
    expect(m.TODO_API_BASE).toBe('/api/todo-web');
  });

  it('treats a bare /todo as the tokenless app', async () => {
    const m = await loadWith({ pathname: '/todo' });
    expect(m.TODO_PUBLIC_BASE).toBe('/todo');
  });

  it('leaves a lookalike path outside the todo app alone', async () => {
    const m = await loadWith({ pathname: '/todos' });
    expect(m.isTodoPublicMode).toBe(false);
  });
});
