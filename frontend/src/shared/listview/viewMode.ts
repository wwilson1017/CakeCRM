/**
 * Board ⇄ list view-mode persistence.
 *
 * sessionStorage, not localStorage, and one key per surface. That matches the
 * existing precedent for board UI state — CRM persists its sort and its filter
 * envelope to sessionStorage deliberately (for a while through
 * `shared/collection`'s own envelope rather than app-local helpers), so a view
 * doesn't leak across sessions. Mixing durabilities
 * (filters reset on a new session but the view mode persists) is the
 * surprising combination, so this follows suit: kanban is always the default
 * on a fresh session.
 *
 * Both functions are total — a list view must never be the reason a board
 * white-screens, and `loadViewMode` runs during first render (`useState`
 * initialiser) where an escaped throw would do exactly that. Private-mode and
 * quota failures are non-fatal by design.
 */
import type { ViewMode } from './types';

export function loadViewMode(storageKey: string): ViewMode {
  try {
    // Anything that isn't exactly 'list' — absent, malformed, a legacy value —
    // falls back to the board, which is every consumer's safe default.
    return sessionStorage.getItem(storageKey) === 'list' ? 'list' : 'kanban';
  } catch {
    return 'kanban';
  }
}

export function saveViewMode(storageKey: string, mode: ViewMode): void {
  try {
    sessionStorage.setItem(storageKey, mode);
  } catch {
    /* sessionStorage unavailable (private mode / quota) — non-fatal */
  }
}
