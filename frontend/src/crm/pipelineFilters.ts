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
 * deal model: stages are strings (not numeric ids), there is no owner concept (single-
 * user v1), and "open" is computed from the stage (deals have no `status` field).
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
/** Relative last-activity buckets (single-select). Activity is DEAL-scoped
 *  (deal-level activity_log rows + un-archived deal chatter); contact-level
 *  activity is not counted — hence "no activity logged", not "never contacted". */
export type ActivityPreset = 'le7' | 'le30' | 'stale30' | 'none';

export interface AdvancedFilters {
  /** Stage keys to include. Empty = all stages. */
  stages: string[];
  /** Inclusive minimum deal value, or null for no lower bound. */
  valueMin: number | null;
  /** Inclusive maximum deal value, or null for no upper bound. */
  valueMax: number | null;
  closeDate: ClosePreset | null;
  lastActivity: ActivityPreset | null;
}

export const EMPTY_ADVANCED: AdvancedFilters = {
  stages: [],
  valueMin: null,
  valueMax: null,
  closeDate: null,
  lastActivity: null,
};

/** Full persisted filter state: free-text search plus the advanced facets, kept in
 *  one envelope so there is a single load/save/clear path. (The blueprint also carried
 *  an owner pill; CakeCRM has no owner, so it is dropped, not deferred.) */
export interface PipelineFilterState {
  search: string;
  advanced: AdvancedFilters;
}

export const EMPTY_FILTER_STATE: PipelineFilterState = {
  search: '',
  advanced: EMPTY_ADVANCED,
};

// ── Date helpers (local timezone) ───────────────────────────────────────────

/** Local YYYY-MM-DD for `base` shifted by `offsetDays`. */
export function ymd(base: Date, offsetDays = 0): string {
  return new Date(base.getTime() + offsetDays * 86_400_000).toLocaleDateString('en-CA');
}

/** The date portion (YYYY-MM-DD) of a stored timestamp, or '' if absent.
 *  Deal timestamps arrive as the DB session's offset (UTC by default); comparing
 *  their date part against local `ymd(now)` can skew by up to one day at bucket
 *  edges near midnight — accepted (matches the blueprint). */
function datePart(ts: string | null | undefined): string {
  return ts ? ts.slice(0, 10) : '';
}

// ── Predicate ───────────────────────────────────────────────────────────────

function matchesValue(deal: CrmDeal, f: AdvancedFilters): boolean {
  const v = deal.value ?? 0;
  if (f.valueMin !== null && v < f.valueMin) return false;
  if (f.valueMax !== null && v > f.valueMax) return false;
  return true;
}

function matchesCloseDate(deal: CrmDeal, preset: ClosePreset, now: Date): boolean {
  const close = datePart(deal.expected_close_date);
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
  const act = datePart(deal.last_activity_at);
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

/** True if `deal` passes every active advanced facet (AND across facets). */
export function dealMatchesAdvanced(deal: CrmDeal, f: AdvancedFilters, now: Date): boolean {
  if (f.stages.length > 0 && !f.stages.includes(deal.stage)) return false;
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
  if (f.valueMin !== null || f.valueMax !== null) n++;
  if (f.closeDate) n++;
  if (f.lastActivity) n++;
  return n;
}

export function hasAdvanced(f: AdvancedFilters): boolean {
  return advancedActiveCount(f) > 0;
}

// ── Session persistence ─────────────────────────────────────────────────────

const STORAGE_KEY = 'crm_pipeline_filters';

const CLOSE_PRESETS: ClosePreset[] = ['overdue', 'next7', 'thisMonth', 'noDate'];
const ACTIVITY_PRESETS: ActivityPreset[] = ['le7', 'le30', 'stale30', 'none'];

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
          ? adv.stages.filter((s): s is string => typeof s === 'string' && STAGE_ORDER.includes(s))
          : [],
        valueMin: coerceNumOrNull(adv.valueMin),
        valueMax: coerceNumOrNull(adv.valueMax),
        closeDate: CLOSE_PRESETS.includes(adv.closeDate as ClosePreset) ? (adv.closeDate as ClosePreset) : null,
        lastActivity: ACTIVITY_PRESETS.includes(adv.lastActivity as ActivityPreset) ? (adv.lastActivity as ActivityPreset) : null,
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
