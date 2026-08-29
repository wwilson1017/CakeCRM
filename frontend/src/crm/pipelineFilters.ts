/**
 * Pure, framework-free filtering logic for the CRM pipeline board (issue #21).
 *
 * The pipeline loads every LIVE deal client-side (`GET /api/crm/deals` with no params →
 * `get_pipeline()`), so filtering happens in the browser over already-loaded data —
 * instant results, no round-trip per keystroke. This module holds the data model +
 * predicate so it can be reasoned about and unit-tested independently of the React
 * component.
 *
 * One deliberate exception since issue #83: the `archived` facet also widens the fetch
 * (`?include_archived=true`), because archived deals are swept out of the payload
 * server-side and a client predicate cannot filter rows it never received. The predicate
 * here still covers all three states, so the state and the payload converge rather than
 * one waiting on the other.
 *
 * Adapted from the CAKE OS blueprint (`apps/crm/pipelineFilters.ts`) to CakeCRM's flat
 * deal model: stages are strings (not numeric ids) and "open" is computed from the
 * stage (deals have no `status` field). The owner facet the blueprint carried was
 * dropped at #21 because CakeCRM was single-user; #60 gave records an owner, so it is
 * back — and stays client-side, because the board already holds every deal.
 *
 * Date semantics intentionally use LOCAL dates (YYYY-MM-DD via en-CA), never
 * `toISOString()`, which would drift a day in US evening time. `now` is injected into
 * the predicate so the logic stays pure and time-deterministic for testing.
 */
import type { CrmDeal } from '../core/types';
import { STAGE_ORDER, OPEN_STAGES } from './constants';

// ── Filter model ────────────────────────────────────────────────────────────

/** Relative close-date buckets (single-select). */
export type ClosePreset = 'overdue' | 'next7' | 'thisMonth' | 'noDate';
/** An owner filter entry: a user id, or the literal 'unassigned' for owner_id NULL.
 *  Unassigned is a real bucket, not an absence — it is how you find work nobody has
 *  picked up, which is the main thing this facet is for. */
export type OwnerFilterValue = number | 'unassigned';

/** Relative last-activity buckets (single-select). Activity is DEAL-scoped
 *  (deal-level activity_log rows + un-archived deal chatter); contact-level
 *  activity is not counted — hence "no activity logged", not "never contacted". */
export type ActivityPreset = 'le7' | 'le30' | 'stale30' | 'none';

/** Archived-deal visibility (issue #83). `null` = live deals only, the default and what
 *  the server returns unasked. `'include'` shows archived deals alongside live ones;
 *  `'only'` is the recovery view — "where did that deal go?".
 *
 *  This is the ONE facet that also widens the FETCH: archived deals are swept out of
 *  `get_pipeline()` server-side, so a purely client-side predicate would have nothing to
 *  filter. `PipelinePage` keys `?include_archived=true` off this being non-null. The
 *  predicate below still enforces all three states client-side, which is what makes the
 *  window between flipping the facet and the new payload landing render correctly. */
export type ArchivedPreset = 'include' | 'only';

export interface AdvancedFilters {
  /** Stage keys to include. Empty = all stages. */
  stages: string[];
  /** Owners to include. Empty = every owner (and unassigned). */
  owners: OwnerFilterValue[];
  /** Inclusive minimum deal value, or null for no lower bound. */
  valueMin: number | null;
  /** Inclusive maximum deal value, or null for no upper bound. */
  valueMax: number | null;
  closeDate: ClosePreset | null;
  lastActivity: ActivityPreset | null;
  /** Archived-deal visibility. null = live only. See `ArchivedPreset`. */
  archived: ArchivedPreset | null;
}

export const EMPTY_ADVANCED: AdvancedFilters = {
  stages: [],
  owners: [],
  valueMin: null,
  valueMax: null,
  closeDate: null,
  lastActivity: null,
  archived: null,
};

/** Full persisted filter state: free-text search plus the advanced facets, kept in
 *  one envelope so there is a single load/save/clear path. */
export interface PipelineFilterState {
  search: string;
  advanced: AdvancedFilters;
}

export const EMPTY_FILTER_STATE: PipelineFilterState = {
  search: '',
  advanced: EMPTY_ADVANCED,
};

// ── Date helpers (local timezone) ───────────────────────────────────────────

/** Local YYYY-MM-DD for a Date, built explicitly from calendar fields with zero-padding.
 *  `toLocaleDateString('en-CA')` is NOT contractually YYYY-MM-DD (locale/ICU availability
 *  can vary the format), and lexicographic comparisons depend on the exact format — so we
 *  format it ourselves. */
function ymdOf(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

/** Local YYYY-MM-DD for `now` shifted by `offsetDays`. Builds the shifted date via calendar
 *  fields (not `getTime() + n*86_400_000`) so a day offset that crosses a DST transition
 *  still lands on the right calendar date near midnight. */
export function ymd(base: Date, offsetDays = 0): string {
  return ymdOf(new Date(base.getFullYear(), base.getMonth(), base.getDate() + offsetDays));
}

/** Close date: stored as a date-only 'YYYY-MM-DD' string (or ''), already a local calendar
 *  date — take it verbatim (parsing 'YYYY-MM-DD' as a Date would read it as UTC midnight and
 *  could shift a day). */
function closeDatePart(ts: string | null | undefined): string {
  return ts ? ts.slice(0, 10) : '';
}

/** Last-activity: a full TIMESTAMPTZ ISO string (DB offset, UTC by default). Parse it and
 *  take the VIEWER'S LOCAL calendar date so the recency buckets are correct in local time
 *  (a slice(0,10) would use the UTC date and misbucket an evening touch near midnight). */
function activityLocalDate(ts: string | null | undefined): string {
  if (!ts) return '';
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? '' : ymdOf(d);
}

// ── Predicate ───────────────────────────────────────────────────────────────

function matchesValue(deal: CrmDeal, f: AdvancedFilters): boolean {
  const v = deal.value ?? 0;
  if (f.valueMin !== null && v < f.valueMin) return false;
  if (f.valueMax !== null && v > f.valueMax) return false;
  return true;
}

function matchesCloseDate(deal: CrmDeal, preset: ClosePreset, now: Date): boolean {
  const close = closeDatePart(deal.expected_close_date);
  switch (preset) {
    case 'noDate':
      return !close;
    case 'overdue':
      // Overdue is only meaningful for still-open deals (deals carry no status field,
      // so "open" = non-terminal stage, shared with the header's open-pipeline total).
      return OPEN_STAGES.includes(deal.stage) && !!close && close < ymd(now);
    case 'next7':
      return !!close && close >= ymd(now) && close <= ymd(now, 7);
    case 'thisMonth':
      return !!close && close.slice(0, 7) === ymd(now).slice(0, 7);
  }
}

function matchesLastActivity(deal: CrmDeal, preset: ActivityPreset, now: Date): boolean {
  const act = activityLocalDate(deal.last_activity_at);
  switch (preset) {
    case 'none':
      return !act;
    case 'le7':
      return !!act && act >= ymd(now, -7);
    case 'le30':
      return !!act && act >= ymd(now, -30);
    case 'stale30':
      // No logged deal activity in the last 30 days — includes deals with none at all.
      return !act || act < ymd(now, -30);
  }
}

function matchesOwner(deal: CrmDeal, owners: OwnerFilterValue[]): boolean {
  // owner_id is absent on a payload from a pre-#60 backend and null when unassigned;
  // both mean "nobody owns this", so both answer to the 'unassigned' bucket.
  const owner = deal.owner_id ?? null;
  return owner === null ? owners.includes('unassigned') : owners.includes(owner);
}

/** True when a deal has been soft-archived (issue #22's `deals.archived_at`; NULL = live).
 *  The single archived predicate for the whole board — the facet, the money aggregates,
 *  the bulk selection and the drag gate all ask this one function, so they cannot drift
 *  about what "archived" means. */
export function isArchivedDeal(deal: CrmDeal): boolean {
  return deal.archived_at != null;
}

/** True if `deal` passes every active advanced facet (AND across facets). */
export function dealMatchesAdvanced(deal: CrmDeal, f: AdvancedFilters, now: Date): boolean {
  // Archived first: it is the cheapest check, and unlike every other facet it is
  // enforced on BOTH sides. The server has already excluded archived deals unless the
  // facet is on, so this is belt-and-braces there — but it is load-bearing in the window
  // after the facet is cleared, when archived rows are still in state and the narrowing
  // refetch has not landed yet.
  const archived = isArchivedDeal(deal);
  if (f.archived === null && archived) return false;
  if (f.archived === 'only' && !archived) return false;
  if (f.stages.length > 0 && !f.stages.includes(deal.stage)) return false;
  if (f.owners.length > 0 && !matchesOwner(deal, f.owners)) return false;
  if (!matchesValue(deal, f)) return false;
  if (f.closeDate && !matchesCloseDate(deal, f.closeDate, now)) return false;
  if (f.lastActivity && !matchesLastActivity(deal, f.lastActivity, now)) return false;
  return true;
}

// ── Active-state helpers ────────────────────────────────────────────────────

/** Number of distinct advanced facets currently constraining results. */
export function advancedActiveCount(f: AdvancedFilters): number {
  let n = 0;
  if (f.stages.length > 0) n++;
  if (f.owners.length > 0) n++;
  if (f.valueMin !== null || f.valueMax !== null) n++;
  if (f.closeDate) n++;
  if (f.lastActivity) n++;
  if (f.archived) n++;
  return n;
}

export function hasAdvanced(f: AdvancedFilters): boolean {
  return advancedActiveCount(f) > 0;
}

// ── Session persistence ─────────────────────────────────────────────────────

const STORAGE_KEY = 'crm_pipeline_filters';

const CLOSE_PRESETS: ClosePreset[] = ['overdue', 'next7', 'thisMonth', 'noDate'];
const ACTIVITY_PRESETS: ActivityPreset[] = ['le7', 'le30', 'stale30', 'none'];
const ARCHIVED_PRESETS: ArchivedPreset[] = ['include', 'only'];

function coerceNumOrNull(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/** Load persisted filter state, tolerating malformed/legacy payloads. Restored stage
 *  keys are validated against `STAGE_ORDER` so a stale/corrupt payload can never hide
 *  every column (an unknown stage would match no deal AND leave no visible column). */
export function loadFilterState(): PipelineFilterState {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return EMPTY_FILTER_STATE;
    const p = JSON.parse(raw) as Partial<PipelineFilterState>;
    const adv = (p.advanced ?? {}) as Partial<AdvancedFilters>;
    return {
      search: typeof p.search === 'string' ? p.search : '',
      advanced: {
        stages: Array.isArray(adv.stages)
          ? [...new Set(adv.stages.filter((s): s is string => typeof s === 'string' && STAGE_ORDER.includes(s)))]
          : [],
        // Owner ids are NOT validated against the live roster: this runs before the
        // users fetch resolves, and a deactivated owner is still a legitimate filter.
        // A stale id simply matches no deal, and the pill stays clearable — unlike a
        // bad stage, which would also hide the column.
        owners: Array.isArray(adv.owners)
          ? [...new Set(adv.owners.filter(
              (o): o is OwnerFilterValue =>
                o === 'unassigned' || (typeof o === 'number' && Number.isInteger(o)),
            ))]
          : [],
        valueMin: coerceNumOrNull(adv.valueMin),
        valueMax: coerceNumOrNull(adv.valueMax),
        closeDate: CLOSE_PRESETS.includes(adv.closeDate as ClosePreset) ? (adv.closeDate as ClosePreset) : null,
        lastActivity: ACTIVITY_PRESETS.includes(adv.lastActivity as ActivityPreset) ? (adv.lastActivity as ActivityPreset) : null,
        // A pre-#83 blob has no `archived` key and restores as null — live-only, the
        // default — so an old session can never resume into a widened fetch it never
        // asked for. Same tolerant-per-key rule as every other facet above.
        archived: ARCHIVED_PRESETS.includes(adv.archived as ArchivedPreset) ? (adv.archived as ArchivedPreset) : null,
      },
    };
  } catch {
    return EMPTY_FILTER_STATE;
  }
}

export function saveFilterState(state: PipelineFilterState): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* sessionStorage unavailable (private mode / quota) — non-fatal */
  }
}
