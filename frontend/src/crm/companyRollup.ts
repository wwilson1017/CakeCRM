/**
 * Pure seams behind the Reports page's company rollup (issue #144).
 *
 * Everything here is data-in / data-out so the interesting rules — how a two-table feed is
 * keyed, how a day boundary is drawn, which rows read as archived — are testable without
 * rendering anything. Ported from cake_os `companyRollup.ts` (#2336), minus its
 * `rollupSummary`: the blueprint reduces its headline chips from the returned children,
 * which lets a capped list move a headline number, so this port takes them from the
 * server's own aggregate instead.
 *
 * Where CakeCRM's schema differs, it differs in one place that matters here: the blueprint
 * has a single `deal.status` carrying open/won/lost/archived, while CakeCRM has TWO columns
 * on two different axes — `stage` (won and lost ARE stages) and `archived_at` (NULL = live)
 * — and contacts archive on a third, `status`. So "is this row archived" takes a predicate
 * per entity rather than reading one shared column.
 */
import type { CrmContact, CrmTimelineEntry } from '../core/types';
import { isArchivedDeal } from './pipelineFilters';

/**
 * Above this many rows in a section, the "Expand all" control is WITHHELD (not disabled).
 *
 * The blueprint's reason was network — each expanded row fetched its own custom fields and
 * tasks, so one click was an account-sized request storm. That reason is gone here: the
 * rollup embeds both, batched, and an expanded row issues no request at all. What remains
 * is DOM: every field of every record of a 200-row section rendered at once is a real cost
 * on a modest machine. Per-row expansion and "Collapse all" stay available at any count,
 * which is why withholding the one bulk control is enough.
 */
export const EXPAND_ALL_MAX = 50;

/** The report's one archived predicate per axis, so no caller re-derives either. */
export { isArchivedDeal };
export const isArchivedContact = (c: CrmContact): boolean => c.status === 'archived';

/**
 * Live rows first, archived after, relative input order preserved inside each half, input
 * never mutated. Takes the predicate rather than reading a column, because deals and contacts
 * are archived on different ones.
 */
export function partitionArchived<T>(
  rows: T[],
  isArchived: (row: T) => boolean,
): { live: T[]; archived: T[] } {
  const live: T[] = [];
  const archived: T[] = [];
  for (const row of rows) (isArchived(row) ? archived : live).push(row);
  return { live, archived };
}

/**
 * The only safe identity for a timeline row.
 *
 * `id` alone is NOT unique across the feed: notes come from `crm_chatter` and activities from
 * `activity_log`, two tables with independent SERIAL sequences, so note #2 and activity #2
 * both exist and are different rows. Using the bare id as a React key or a dedupe key would
 * silently drop one of them.
 */
export const entryKey = (entry: CrmTimelineEntry): string => `${entry.source}:${entry.id}`;

/**
 * Append a page, dropping only rows already held — keyed by (source, id), never by id.
 * `prev` is not mutated, and its order is preserved.
 */
export function appendTimelinePage(
  prev: CrmTimelineEntry[],
  incoming: CrmTimelineEntry[],
): CrmTimelineEntry[] {
  const seen = new Set(prev.map(entryKey));
  return [...prev, ...incoming.filter((entry) => !seen.has(entryKey(entry)))];
}

/**
 * Group CONSECUTIVE same-day entries, returning ordered `[label, entries][]` pairs.
 *
 * Relies on the server's newest-first order and never re-sorts. Pairs rather than a Record so
 * iteration order is explicit rather than a property of object key ordering.
 *
 * The zone handling mirrors `shared/formatDate`: a Postgres TIMESTAMPTZ already serializes
 * with an offset, so 'Z' is appended only to a bare naive string. Getting this wrong moves
 * rows across a day boundary, which is exactly the drift a date header would hide.
 */
export function groupTimelineByDate(
  entries: CrmTimelineEntry[],
): [string, CrmTimelineEntry[]][] {
  const groups: [string, CrmTimelineEntry[]][] = [];
  for (const entry of entries) {
    const label = dayLabel(entry.created_at);
    const last = groups[groups.length - 1];
    if (last && last[0] === label) last[1].push(entry);
    else groups.push([label, [entry]]);
  }
  return groups;
}

function dayLabel(iso: string): string {
  try {
    const hasZone = /[Zz]|[+-]\d\d:?\d\d$/.test(iso);
    const d = new Date(hasZone ? iso : iso + 'Z');
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' });
  } catch {
    return iso;
  }
}

export interface TimelineSource {
  kind: 'Company' | 'Contact' | 'Deal';
  name: string;
  archived: boolean;
}

const KINDS = { company: 'Company', contact: 'Contact', deal: 'Deal' } as const;

/**
 * The source chip. Carries no "linkable" flag deliberately: an archived deal is still
 * reachable — `GET /api/crm/deals/:id` has no live filter and the detail sheet renders an
 * archived deal so it can be restored. Withholding the link would strand exactly the record a
 * reader most needs to reach.
 */
export function timelineSourceLabel(entry: CrmTimelineEntry): TimelineSource {
  return { kind: KINDS[entry.entity_type], name: entry.source_name, archived: entry.source_archived };
}

/**
 * One line of text for an entry. An activity leads with its kind so a bare logged call still
 * reads as something ("call") rather than as an empty row; a note is its own message.
 */
export function describeTimelineEntry(entry: CrmTimelineEntry): string {
  if (entry.source === 'note') return entry.message;
  const kind = entry.activity ? entry.activity.replace(/_/g, ' ') : 'activity';
  return entry.message ? `${kind} · ${entry.message}` : kind;
}
