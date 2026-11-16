import type { ConfirmSummary } from './types';

// CakeCRM — what a bulk-create Approve card (#284) lists. See BulkItemList.

/** Rows shown before "Show all" on a long batch. */
export const COLLAPSED_ROWS = 20;

/** The items of an args object whose `todos` is an array, or null for any other call. */
export function bulkItems(args: Record<string, unknown> | undefined): unknown[] | null {
  const todos = args?.todos;
  return Array.isArray(todos) ? todos : null;
}

/** A server-built card summary (#370), or null for anything else. */
export function confirmSummary(value: unknown): ConfirmSummary | null {
  if (!value || typeof value !== 'object') return null;
  const { headline, lines } = value as { headline?: unknown; lines?: unknown };
  if (typeof headline !== 'string' || !Array.isArray(lines)) return null;
  return { headline, lines: lines.filter((l): l is string => typeof l === 'string') };
}

/** The staged change list (#371) a confirm event, a pending placeholder or a result names. */
export function changeBatchId(value: unknown): string | null {
  const id = value && typeof value === 'object'
    ? (value as { change_batch_id?: unknown }).change_batch_id : null;
  return typeof id === 'string' && id ? id : null;
}
