// CakeCRM — what a bulk-create Approve card (#284) lists. See BulkItemList.

/** Rows shown before "Show all" on a long batch. */
export const COLLAPSED_ROWS = 20;

/** The items of an args object whose `todos` is an array, or null for any other call. */
export function bulkItems(args: Record<string, unknown> | undefined): unknown[] | null {
  const todos = args?.todos;
  return Array.isArray(todos) ? todos : null;
}
