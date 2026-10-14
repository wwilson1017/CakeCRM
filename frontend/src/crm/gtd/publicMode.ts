/**
 * Todo-GTD — no-login public web-app mode (#70; browser sessions since #293).
 *
 * The backend serves the SPA shell at ONE fixed address, /todo, for every signed-in
 * browser (see backend/crm/todo_web.py). The browser's HttpOnly cookie says which
 * session it is, so nothing secret is in the URL and nothing is injected into the page.
 * In that mode there is no login and no CRM chrome — just the todo app, talking to
 * /api/todo-web instead of /api/crm/gtd, over RAW fetch (never the shared api() helper,
 * which 401-redirects to /login — exactly what a no-login surface must never do).
 */

function detectBase(): string | null {
  const path = window.location.pathname;
  return path === '/todo' || path.startsWith('/todo/') ? '/todo' : null;
}

/** Router basename when serving the no-login todo app, else null. */
export const TODO_PUBLIC_BASE = detectBase();

export const isTodoPublicMode = TODO_PUBLIC_BASE !== null;

/** The no-login app's API root. */
export const TODO_WEB_API = '/api/todo-web';

/** API prefix standing in for /api/crm/gtd in public mode. */
export const TODO_API_BASE = isTodoPublicMode ? TODO_WEB_API : '/api/crm/gtd';

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
