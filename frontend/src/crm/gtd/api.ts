// Todo-GTD — typed API calls.
//
// Every call goes through `todoFetch`, which is mode-aware: in the CRM it delegates
// to the shared api() helper (JWT, 401 → /login); in no-login public mode it hits the
// session-guarded /api/todo-web mount via RAW fetch. Raw fetch is mandatory there —
// api() 401-redirects to /login, which is precisely what a no-login surface must never
// do. A 401 there flips the session store to signed out instead (#293). The two modes share ONE client so neither needs its own copy of the
// pages.

import { api, ApiError } from '../../core/api/client';
import { TODO_API_BASE, TODO_WEB_API, isTodoPublicMode } from './publicMode';
import { signedOut } from './session';
import type { Todo, TodoFilters, TodoProject, TodoStatus } from './types';
import { parseUTC } from './util';

/** Path relative to the todo API root (e.g. '/todos', '/today', '/projects/3'). */
function todoFetch<T = unknown>(sub: string, options: RequestInit = {}): Promise<T> {
  if (!isTodoPublicMode) return api<T>(`${TODO_API_BASE}${sub}`, options);
  return rawTodoFetch<T>(`${TODO_API_BASE}${sub}`, options);
}

async function rawTodoFetch<T>(url: string, options: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers as Record<string, string>) },
  });
  if (!res.ok) {
    // A 401 means this browser's session ended: show the signed-out page, never /login.
    if (res.status === 401) signedOut();
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch { /* non-JSON body — keep statusText */ }
    throw new ApiError(`API error ${res.status}: ${detail}`, res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// ── The no-login app's session (#293) ──────────────────────────────────────────
// Raw fetch against the web mount only. The cookie rides along on its own (same origin).

/** 200 with a live session; rejects with a 401 ApiError when signed out. */
export function getSession(): Promise<{ ok: true }> {
  return rawTodoFetch(`${TODO_WEB_API}/session`, {});
}

/** Spend a sign-in code: the response sets this browser's session cookie. */
export function redeemSignIn(token: string): Promise<{ ok: true }> {
  return rawTodoFetch(`${TODO_WEB_API}/signin/${encodeURIComponent(token)}`, { method: 'POST' });
}

/** Sign THIS browser out. Every other device stays signed in. */
export function signOut(): Promise<{ ok: true }> {
  return rawTodoFetch(`${TODO_WEB_API}/signout`, { method: 'POST' });
}

export interface TodoListParams {
  status?: TodoStatus;
  project?: string;
  context?: string;
  tag?: string;
  starred?: boolean;
  due_before?: string;
  due_after?: string;
  search?: string;
  limit?: number;
  /** Every match instead of one `limit` window (#295): a list page has no "load more", and
   * one window is only the 500 OLDEST rows. Oldest first; `limit` is ignored. */
  all?: boolean;
  /** Keep todos waiting on a bring-back date (#261). Record views and project-health
   * checks only — the working lists leave them out until their day. */
  include_deferred?: boolean;
}

function fetchTodos(params: object, signal?: AbortSignal): Promise<Todo[]> {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') q.set(k, String(v));
  }
  const qs = q.toString();
  return todoFetch<{ todos: Todo[] }>(`/todos${qs ? `?${qs}` : ''}`, { signal }).then(r => r.todos);
}

/** Rows kept per sweep page. The route caps `limit` at 500 and the sweep asks for one more. */
const SWEEP_PAGE = 499;

/**
 * Every todo matching `params`, in the lists' own oldest-first order (#295).
 *
 * A KEYSET walk on the immutable id (`sort=id` + `after_id`), never OFFSET, with "is there
 * more" read off one extra row rather than a count — the CRM list sweep's rules
 * (docs/agents/list-pages.md), restated here because this module also rides in the /todo
 * PWA, which may reach no CRM module. Aborting `signal` stops the walk at its next page.
 *
 * simplification: the whole list is held and rendered at once. A list of tens of thousands
 * of open todos needs server-side filtering and a virtualized list, not a bigger page.
 */
async function sweepTodos(params: TodoListParams, signal?: AbortSignal): Promise<Todo[]> {
  const all: Todo[] = [];
  let after: number | undefined;
  for (;;) {
    const page = await fetchTodos(
      { ...params, sort: 'id', limit: SWEEP_PAGE + 1, after_id: after }, signal,
    );
    // A server that ignores the cursor would hand back page one forever.
    if (after !== undefined && page.length > 0 && page[0].id <= after) {
      throw new Error('Todo list sweep did not advance');
    }
    const kept = page.slice(0, SWEEP_PAGE);
    all.push(...kept);
    if (page.length <= SWEEP_PAGE) break;
    after = kept[kept.length - 1].id;
  }
  // The walk comes back in id order; the lists show created order (the server's default).
  return all.sort((a, b) =>
    parseUTC(a.created_at).getTime() - parseUTC(b.created_at).getTime() || a.id - b.id);
}

export function listTodos(
  { all, ...params }: TodoListParams = {}, signal?: AbortSignal,
): Promise<Todo[]> {
  return all ? sweepTodos(params, signal) : fetchTodos(params, signal);
}

export function todayTodos(): Promise<Todo[]> {
  return todoFetch<{ todos: Todo[] }>('/today').then(r => r.todos);
}

export function createTodo(
  body: Partial<Todo> & { title: string; project?: string | null },
): Promise<Todo> {
  return todoFetch<Todo>('/todos', { method: 'POST', body: JSON.stringify(body) });
}

export function updateTodo(id: number, fields: Record<string, unknown>): Promise<Todo> {
  return todoFetch<Todo>(`/todos/${id}`, { method: 'PUT', body: JSON.stringify(fields) });
}

export function bulkUpdate(
  ids: number[], fields: Record<string, unknown>,
): Promise<{ updated: number[]; not_found: number[] }> {
  return todoFetch('/todos/bulk', { method: 'POST', body: JSON.stringify({ ids, fields }) });
}

export function listProjects(status?: string): Promise<TodoProject[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : '';
  return todoFetch<{ projects: TodoProject[] }>(`/projects${qs}`).then(r => r.projects);
}

export function createProject(
  body: { name: string; notes?: string; status?: string; purpose?: string; outcome?: string },
): Promise<TodoProject> {
  return todoFetch<TodoProject>('/projects', { method: 'POST', body: JSON.stringify(body) });
}

export function updateProject(id: number, fields: Record<string, unknown>): Promise<TodoProject> {
  return todoFetch<TodoProject>(`/projects/${id}`, { method: 'PUT', body: JSON.stringify(fields) });
}

export function deleteProject(id: number): Promise<void> {
  return todoFetch(`/projects/${id}`, { method: 'DELETE' }).then(() => undefined);
}

export function getFilters(): Promise<TodoFilters> {
  return todoFetch<TodoFilters>('/filters');
}

/** Rename a context on every todo carrying it (#280). 409 = `next` already exists;
 *  resend with `merge` once the user has confirmed. Authed CRM only: the route is not on
 *  the no-login web mount, so this never goes through the public raw-fetch path. */
export function renameContext(
  old: string, next: string, merge = false,
): Promise<{ count: number; todo_ids: number[]; merged: boolean }> {
  return api(`${TODO_API_BASE}/contexts/rename`, {
    method: 'POST', body: JSON.stringify({ old, new: next, merge }),
  });
}

/** Whether a weekly review is due (#263). The full review packet rides the same
 *  response; the Review page reads only these two fields. */
export interface ReviewStatus {
  review_due: boolean;
  days_since_review: number | null;
}

export function getReviewStatus(): Promise<ReviewStatus> {
  return todoFetch<ReviewStatus>('/review');
}

export function markReviewDone(): Promise<ReviewStatus> {
  return todoFetch<ReviewStatus>('/review/done', { method: 'POST' });
}
