/**
 * Todo-GTD — the no-login app's session state (#293, port of todo-gtd `session.ts`).
 *
 * State and subscriptions only. `PublicTodoApp`'s gate asks the server once per page
 * load (`getSession`), and `api.ts` calls `signedOut()` on any 401 so the app swaps to
 * the signed-out page wherever the session ends. Signed out is a STATE, never a
 * navigation: nothing here sends anyone to the CRM login.
 */
import { useSyncExternalStore } from 'react';

export type SessionKind = 'loading' | 'ok' | 'signedOut' | 'error';

let state: SessionKind = 'loading';
const listeners = new Set<() => void>();

export function sessionState(): SessionKind {
  return state;
}

export function setSession(next: SessionKind): void {
  if (next === state) return;
  state = next;
  for (const fn of listeners) fn();
}

export function signedOut(): void {
  setSession('signedOut');
}

export function resetSession(): void {
  setSession('loading');
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function useSession(): SessionKind {
  return useSyncExternalStore(subscribe, sessionState, sessionState);
}
