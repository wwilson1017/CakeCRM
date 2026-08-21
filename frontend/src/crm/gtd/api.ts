// Todo-GTD — typed API calls.
//
// Every call goes through `todoFetch`, which is mode-aware: in the CRM it delegates
// to the shared api() helper (JWT, 401 → /login); in no-login public mode it hits the
// token-guarded /api/todo-web/{token} mount via RAW fetch. Raw fetch is mandatory
// there — api() 401-redirects to /login, which is precisely what a no-login surface
// must never do. The two modes share ONE client so neither needs its own copy of the
// pages.

import { api } from '../../core/api/client';
import { TODO_API_BASE, isTodoPublicMode } from './publicMode';
import type { Todo, TodoFilters, TodoProject, TodoStatus } from './types';

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
    // A 404 here means an unknown or rotated token; it surfaces as an ordinary
    // error the page explains, and nothing redirects to /login.
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch { /* non-JSON body — keep statusText */ }
    throw new Error(`API error ${res.status}: ${detail}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
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
}

export function listTodos(params: TodoListParams = {}): Promise<Todo[]> {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') q.set(k, String(v));
  }
  const qs = q.toString();
  return todoFetch<{ todos: Todo[] }>(`/todos${qs ? `?${qs}` : ''}`).then(r => r.todos);
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

export function deleteTodo(id: number): Promise<void> {
  return todoFetch(`/todos/${id}`, { method: 'DELETE' }).then(() => undefined);
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
  body: { name: string; notes?: string; status?: string },
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
