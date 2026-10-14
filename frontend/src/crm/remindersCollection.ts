/**
 * remindersCollection — the Reminders page's `CollectionConfig` (issue #283).
 *
 * Pure data, like `pipelineCollection.ts`: the page passes its columns in. Facets are
 * client-side over the whole swept corpus (list-pages rule), so there are no server filters.
 *
 * "Mine / All" is a `single` facet whose resting (unselected) state is All, so it needs no
 * custom renderer. Mine = reminders I set or receive, the server's `scope=mine` rule.
 */

import { dateRangeFacet } from '../shared/collection';
import type { CollectionConfig, FacetDef } from '../shared/collection';
import type { FacetOption, SortState } from '../shared/search';
import type { ListColumn } from '../shared/listview';
import type { CrmReminder, ReminderRepeat } from '../core/types';
import { dueLabel, parseUTC } from './gtd/util';

export const REMINDER_STATUS_OPTIONS: FacetOption[] = [
  { value: 'upcoming', label: 'Upcoming' },
  { value: 'fired', label: 'Sent' },
  { value: 'voided', label: 'Voided' },
];

export const REMINDER_SCOPE_OPTIONS: FacetOption[] = [{ value: 'mine', label: 'Mine' }];

export const REPEAT_LABELS: Record<ReminderRepeat, string> = {
  daily: 'Every day', weekly: 'Every week', monthly: 'Every month', yearly: 'Every year',
};

/** Mine = I set it, or I am one of its people. */
export function isMine(r: CrmReminder, meId: number | null): boolean {
  return meId !== null && (r.owner_id === meId || r.people.includes(meId));
}

/** Sort on the parsed instant, never the string (an offset makes strings misorder). */
const dueMillis = (r: CrmReminder) => parseUTC(r.due_at).getTime();

export const REMINDER_SORT_FIELDS = [
  { value: 'due', label: 'Due', get: dueMillis },
  { value: 'note', label: 'Note', get: (r: CrmReminder) => r.note.toLowerCase() || null },
  { value: 'status', label: 'Status', get: (r: CrmReminder) => r.status },
] as const;

export const REMINDER_DEFAULT_SORT: SortState = { field: 'due', dir: 'asc' };

export interface RemindersConfigDeps {
  columns: ListColumn<CrmReminder>[];
  meId: number | null;
}

export function makeRemindersCollectionConfig(deps: RemindersConfigDeps): CollectionConfig<CrmReminder> {
  const facets: FacetDef<CrmReminder>[] = [
    {
      kind: 'single', key: 'scope', label: 'Show',
      options: REMINDER_SCOPE_OPTIONS,
      predicate: (r, v) => v !== 'mine' || isMine(r, deps.meId),
    },
    { key: 'status', label: 'Status', getValue: r => r.status, options: REMINDER_STATUS_OPTIONS },
    dateRangeFacet<CrmReminder>({ key: 'dueRange', label: 'Due', getDay: r => r.due_date }),
  ];
  return {
    storage: { key: 'crm_reminders', version: 1 },
    defaultView: 'list',
    getItemId: r => r.id,
    searchText: r => [r.note],
    persistSearch: true,
    facets,
    sort: { fields: REMINDER_SORT_FIELDS, defaultSort: REMINDER_DEFAULT_SORT },
    list: { columns: deps.columns },
    detail: {
      getTitle: r => r.note,
      getSubtitle: r => (r.repeat ? REPEAT_LABELS[r.repeat] : null),
    },
    itemNoun: { singular: 'reminder', plural: 'reminders' },
    emptyState: { message: 'No reminders to show.' },
  };
}

/** "Today · 15:00", "Mon Oct 20 · morning". */
export function reminderWhen(r: Pick<CrmReminder, 'due_date' | 'due_time'>, today: string): string {
  return `${dueLabel(r.due_date, today).text} · ${r.due_time ?? 'morning'}`;
}

type ReminderBody = Pick<CrmReminder, 'note' | 'people' | 'repeat' | 'due_date' | 'due_time'>;

/**
 * Only what the user changed. The server re-checks the schedule whenever the date or time
 * is in the patch, so resending an unchanged time that has since passed would refuse an
 * ordinary note edit on a reminder the tick has not reached yet.
 */
export function changedFields(before: CrmReminder, after: ReminderBody): Partial<ReminderBody> {
  const out: Partial<ReminderBody> = {};
  if (after.note !== before.note) out.note = after.note;
  if ([...after.people].sort().join() !== [...before.people].sort().join()) out.people = after.people;
  if (after.repeat !== before.repeat) out.repeat = after.repeat;
  if (after.due_date !== before.due_date || after.due_time !== before.due_time) {
    out.due_date = after.due_date;
    out.due_time = after.due_time;
  }
  return out;
}
