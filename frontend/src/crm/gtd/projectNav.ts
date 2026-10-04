// Todo GTD — previous / next project for the project detail page's arrows (#264, port of
// todo-gtd 382ce21).
import type { TodoProject } from './types';

type NavProject = Pick<TodoProject, 'id' | 'name' | 'status'>;

/**
 * The neighbours of `currentId` among the projects sharing its status, in LIST order,
 * wrapping at both ends. Null when the project is unknown or is the only one of its
 * status — there is nothing to cycle to.
 *
 * List order is deliberately not re-derived here. `projects` is the shared meta cache, which
 * comes from the same `listProjects()` endpoint the Projects page reads, and that page's
 * collection config declares no `sort` — so the server's `lower(name)` order (a total order:
 * names are unique case-insensitively) filtered to one status IS the order of that status's
 * tab. The page's search box is not applied: it is a transient lookup, and a stale query
 * from earlier in the session would otherwise silently shrink the cycle.
 */
export function projectNeighbours(
  projects: NavProject[], currentId: number,
): { prev: NavProject; next: NavProject } | null {
  const status = projects.find(p => p.id === currentId)?.status;
  const peers = projects.filter(p => p.status === status);
  const i = peers.findIndex(p => p.id === currentId);
  if (i < 0 || peers.length < 2) return null;
  return {
    prev: peers[(i - 1 + peers.length) % peers.length],
    next: peers[(i + 1) % peers.length],
  };
}

/**
 * True when a key press belongs to a control the user is typing in or operating with the
 * arrow keys: the inline name / notes editors (an `<input>` and a `<textarea>`), the
 * add-a-next-action box, Quick Add, the status `<select>`, and anything contenteditable.
 */
export function isTypingTarget(el: EventTarget | null): boolean {
  return el instanceof HTMLElement
    && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName));
}
