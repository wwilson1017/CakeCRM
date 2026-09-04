/**
 * Pure seams behind the Reports page's company rollup (issue #144).
 *
 * Everything here is data-in / data-out so the interesting rules — what counts as an open
 * deal, how a two-table feed is keyed, how a day boundary is drawn — are testable without
 * rendering anything. Ported from cake_os `companyRollup.ts` (#2336) and adapted where
 * CakeCRM's schema differs, which is mostly one place: the blueprint has a single
 * `deal.status` field carrying open/won/lost/archived, and CakeCRM has TWO columns on two
 * different axes — `stage` (won/lost are stages) and `archived_at` (NULL = live). A deal can
 * be archived while sitting in an open stage, so every "is this deal counted" question has to
 * ask both.
 */
import type { CrmContact, CrmDeal, CrmTimelineEntry } from '../core/types';
import { OPEN_STAGES } from './constants';
import { isArchivedDeal } from './pipelineFilters';

/**
 * Above this many rows in a section, the "Expand all" control is WITHHELD (not disabled).
 * Each expanded row fires its own reads — custom fields, and for a deal its tasks — so one
 * click on a large account would be an account-sized request storm. Per-row expansion and
 * "Collapse all" stay available at any count.
 */
export const EXPAND_ALL_MAX = 50;

/** The report's one archived predicate per axis, so no caller re-derives either. */
export { isArchivedDeal };
export const isArchivedContact = (c: CrmContact): boolean => c.status === 'archived';

const OPEN_STAGE_SET = new Set<string>(OPEN_STAGES);

/** Live AND in a non-terminal stage. Both columns, because they are different axes. */
export function isOpenDeal(deal: CrmDeal): boolean {
  return !isArchivedDeal(deal) && OPEN_STAGE_SET.has(deal.stage);
}

export interface RollupSummary {
  openDealCount: number;
  openDealValue: number;
  contactCount: number;
  dealsPartial: boolean;
  contactsPartial: boolean;
}

/**
 * The three header chips.
 *
 * A non-finite `value` contributes 0 rather than poisoning the whole sum with NaN — one bad
 * row must not blank the number the page exists to show. The partial flags default to FALSE,
 * never true: claiming a total is incomplete when it isn't is its own kind of wrong.
 */
export function rollupSummary(
  deals: CrmDeal[],
  contacts: CrmContact[],
  truncated: { deals?: boolean; contacts?: boolean } = {},
): RollupSummary {
  let openDealCount = 0;
  let openDealValue = 0;
  for (const deal of deals) {
    if (!isOpenDeal(deal)) continue;
    openDealCount += 1;
    openDealValue += Number.isFinite(deal.value) ? deal.value : 0;
  }
  return {
    openDealCount,
    openDealValue,
    contactCount: contacts.filter((c) => !isArchivedContact(c)).length,
    dealsPartial: truncated.deals === true,
    contactsPartial: truncated.contacts === true,
  };
}

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
