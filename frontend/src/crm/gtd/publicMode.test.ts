// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * publicMode resolves its constants at module load from `window.location`, so each case
 * sets the URL and then re-imports through a reset module registry. Since #293 the app
 * lives at ONE fixed address, /todo, and its browser session is a cookie — nothing about
 * the session is in the URL or injected into the page.
 */
async function loadAt(pathname: string) {
  vi.resetModules();
  window.history.replaceState({}, '', pathname);
  return import('./publicMode');
}

beforeEach(() => { window.history.replaceState({}, '', '/'); });

describe('inside the CRM', () => {
  it('is not public mode and targets the authed API', async () => {
    const m = await loadAt('/crm/todos');
    expect(m.isTodoPublicMode).toBe(false);
    expect(m.TODO_PUBLIC_BASE).toBeNull();
    expect(m.TODO_API_BASE).toBe('/api/crm/gtd');
    expect(m.todoPath('/inbox')).toBe('/crm/todos/inbox');
    expect(m.todoPath()).toBe('/crm/todos');
  });

  it('leaves a lookalike path outside the todo app alone', async () => {
    const m = await loadAt('/todos');
    expect(m.isTodoPublicMode).toBe(false);
  });
});

describe('the no-login app at /todo', () => {
  it.each(['/todo', '/todo/inbox', '/todo/projects/4', '/todo/signin/abc'])(
    '%s is the app at the fixed base and API',
    async (path) => {
      const m = await loadAt(path);
      expect(m.isTodoPublicMode).toBe(true);
      expect(m.TODO_PUBLIC_BASE).toBe('/todo');
      expect(m.TODO_API_BASE).toBe('/api/todo-web');
    },
  );

  it('hangs pages off the router root', async () => {
    const m = await loadAt('/todo/inbox');
    expect(m.todoPath('/inbox')).toBe('/inbox');
    expect(m.todoPath()).toBe('/');
  });
});
