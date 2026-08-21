// Todo-GTD — status metadata for chips and grouping.
//
// The chip classes use the theme's semantic aliases (`bg-sand`, `text-muted`), which
// resolve through the `--color-ck-*` tokens and therefore re-theme under `.dark` with
// no per-component variants. The per-status hues carry explicit dark variants because
// a 100-weight tint is invisible on a dark surface.
import type { TodoProjectStatus, TodoStatus } from './types';

export const STATUS_META: Record<TodoStatus, { label: string; chip: string }> = {
  inbox: { label: 'Inbox', chip: 'bg-sand text-muted' },
  next_action: { label: 'Next', chip: 'bg-green-100 dark:bg-green-950/40 text-green-700 dark:text-green-300' },
  waiting_for: { label: 'Waiting', chip: 'bg-amber-100 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300' },
  delegated: { label: 'Delegated', chip: 'bg-blue-100 dark:bg-blue-950/40 text-blue-700 dark:text-blue-300' },
  someday_maybe: { label: 'Someday', chip: 'bg-purple-100 dark:bg-purple-950/40 text-purple-700 dark:text-purple-300' },
  done: { label: 'Done', chip: 'bg-sand text-muted' },
  dropped: { label: 'Dropped', chip: 'bg-sand text-muted line-through' },
};

export const TODO_STATUS_ORDER: TodoStatus[] = [
  'inbox', 'next_action', 'waiting_for', 'delegated', 'someday_maybe', 'done', 'dropped',
];

/** The statuses a triage step can file an inbox item into (never done/dropped —
 * those are actions, not destinations). */
export const TRIAGE_STATUSES: TodoStatus[] = [
  'next_action', 'waiting_for', 'delegated', 'someday_maybe',
];

export const PROJECT_STATUSES: TodoProjectStatus[] = [
  'active', 'someday', 'completed', 'dropped',
];

export const PROJECT_STATUS_META: Record<TodoProjectStatus, { label: string; chip: string }> = {
  active: { label: 'Active', chip: 'bg-green-100 dark:bg-green-950/40 text-green-700 dark:text-green-300' },
  someday: { label: 'Someday', chip: 'bg-purple-100 dark:bg-purple-950/40 text-purple-700 dark:text-purple-300' },
  completed: { label: 'Completed', chip: 'bg-sand text-muted' },
  dropped: { label: 'Dropped', chip: 'bg-sand text-muted line-through' },
};

export const REPEAT_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: 'No repeat' },
  { value: 'daily', label: 'Daily' },
  { value: 'weekdays', label: 'Weekdays' },
  { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'yearly', label: 'Yearly' },
];

// Days a next/waiting/delegated item can sit untouched before the Review page calls
// it stale.
export const STALE_DAYS = 14;
