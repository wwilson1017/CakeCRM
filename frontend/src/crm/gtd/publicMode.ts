/**
 * Todo-GTD — no-login public web-app mode (#70).
 *
 * The backend serves the SPA shell at /todo (or /todo/{token}) with the router
 * basename injected as `window.__CAKECRM_TODO_BASE__` (see backend/crm/todo_web.py).
 * In that mode there is no login and no CRM chrome — just the todo app, talking to
 * /api/todo-web[/{token}] instead of /api/crm/gtd, over RAW fetch (never the shared
 * api() helper, which 401-redirects to /login — exactly what a no-login surface must
 * never do).
 */

/**
 * Segments after /todo that can never be a secret token — they are the app's own
 * client-side page routes, and the backend's RESERVED_TODO_WEB_SLUGS refuses to
 * store any of them as a token. That is what makes the dev-server fallback below
 * unambiguous: Vite serves index.html for any path without asking the backend, so
 * nothing is injected there and the basename has to be recovered from the URL.
 */
const PAGE_SLUGS = [
  'today', 'inbox', 'next', 'projects', 'waiting', 'someday', 'done', 'review', 'search',
];

function detectBase(): string | null {
  const injected = (window as { __CAKECRM_TODO_BASE__?: string }).__CAKECRM_TODO_BASE__;
  if (typeof injected === 'string' && injected) return injected;

  const parts = window.location.pathname.split('/').filter(Boolean);
  if (parts[0] !== 'todo') return null;
  const second = parts[1];
  return second && !PAGE_SLUGS.includes(second) ? `/todo/${second}` : '/todo';
}

/** Router basename when serving the no-login todo app, else null. */
export const TODO_PUBLIC_BASE = detectBase();

export const isTodoPublicMode = TODO_PUBLIC_BASE !== null;

/** API prefix standing in for /api/crm/gtd — carries the token in public mode. */
export const TODO_API_BASE = TODO_PUBLIC_BASE
  ? `/api/todo-web${TODO_PUBLIC_BASE.slice('/todo'.length)}`
  : '/api/crm/gtd';

/**
 * Route path for a todo page.
 *
 * Inside the CRM these live under /crm/todos (Today at the bare /crm/todos). In
 * public mode the router basename already points at the todo root, so the pages hang
 * off '/'. Pass the sub-path WITHOUT the prefix: todoPath('/inbox') →
 * '/crm/todos/inbox' (authed) or '/inbox' (public).
 */
export function todoPath(sub = ''): string {
  if (isTodoPublicMode) return sub || '/';
  return `/crm/todos${sub}`;
}
