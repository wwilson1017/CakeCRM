/**
 * Pure, framework-free filtering logic for the CRM pipeline board (issue #21).
 *
 * The pipeline loads every deal client-side (`GET /api/crm/deals` with no params →
 * `get_pipeline()` returns all deals), so all filtering happens in the browser over
 * already-loaded data — no server round-trips, no new query params, instant results.
 * This module holds the data model + predicate so it can be reasoned about (and unit-
 * tested, should a runner ever be added) independently of the React component.
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
 *
 * SCOPE NOTE (#74): this module used to own the whole filter envelope — the search text,
 * an active-facet count, and sessionStorage under `crm_pipeline_filters`. All three now
 * belong to the shared collection layer (`collection_crm_pipeline_v1`), which persists,
 * coerces and counts every facet it declares. What stays here is the part the layer cannot
 * know: the PREDICATE, and the two bucket definitions that make it non-obvious —
 * `overdue` counts open deals only (a won deal with a past close date is not overdue), and
 * `stale30` includes never-contacted deals (so `le30` and `stale30` partition the open set
 * rather than leaving a gap). `pipelineCollection.ts` delegates to `dealMatchesAdvanced`
 * for exactly that reason instead of restating the rules in a facet definition.
 */
import type { CrmDeal } from '../core/types';
import { OPEN_STAGES } from './constants';

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
}

export const EMPTY_ADVANCED: AdvancedFilters = {
  stages: [],
  owners: [],
  valueMin: null,
  valueMax: null,
  closeDate: null,
  lastActivity: null,
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

/** True if `deal` passes every active advanced facet (AND across facets). */
export function dealMatchesAdvanced(deal: CrmDeal, f: AdvancedFilters, now: Date): boolean {
  if (f.stages.length > 0 && !f.stages.includes(deal.stage)) return false;
  if (f.owners.length > 0 && !matchesOwner(deal, f.owners)) return false;
  if (!matchesValue(deal, f)) return false;
  if (f.closeDate && !matchesCloseDate(deal, f.closeDate, now)) return false;
  if (f.lastActivity && !matchesLastActivity(deal, f.lastActivity, now)) return false;
  return true;
}
