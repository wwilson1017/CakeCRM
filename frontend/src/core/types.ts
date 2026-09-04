// Shared CRM API types (mirrors the backend crm/service.py response shapes).

export interface CrmContact {
  /** Owner (issue #60). null = unassigned, which is a real state, not missing data.
   *  Ownership is an assignment and a filter, never a permission. */
  owner_id?: number | null;
  id: number;
  name: string;
  email: string;
  phone: string;
  company: string;
  company_id: number | null;
  company_name?: string; // LEFT JOIN companies — contact detail, list, and search responses.
                         // Authoritative over the legacy free-text `company` (issue #35):
                         // render `company_name || company`.
  title: string;
  source: string;
  status: string;
  tags: string;
  notes: string;
  created_at: string;
  updated_at: string;
  lead_score?: number | null;           // computed lead score 0-100 (issue #18); null = never scored
  lead_score_at?: string | null;
  // Derived last touch (issue #77): newest of the contact's activity_log rows and its
  // un-archived, non-housekeeping chatter — the same two signals
  // analytics_service.get_contact_staleness reads. Present on the list, search and detail
  // responses; null = never contacted, a real state and the most urgent one.
  last_contact_at?: string | null;
  // Detail view extras
  deals?: CrmDeal[];
  tasks?: CrmTask[];
  activity?: CrmActivity[];
}

export interface CrmDeal {
  /** Owner (issue #60). null = unassigned, which is a real state, not missing data.
   *  Ownership is an assignment and a filter, never a permission. */
  owner_id?: number | null;
  id: number;
  contact_id: number | null;
  contact_name?: string;
  company_id: number | null;
  company_name?: string; // joined by get_deal AND the pipeline board (issue #21)
  title: string;
  stage: string;
  value: number;
  notes: string;
  expected_close_date: string;
  probability: number;
  currency: string;
  created_at: string;
  updated_at: string;
  ai_touch_count?: number | null;       // AI-estimated touch count (issue #16); null = uncomputed
  ai_touch_count_at?: string | null;
  lost_reason?: string;                 // why a lost deal was lost (issue #22); '' when unset
  archived_at?: string | null;          // soft-archive (issue #22); null = live
  // Pipeline board only (issue #21): MAX of the deal's activity_log rows + un-archived
  // deal chatter notes; null = no logged activity. Not present on detail-path responses.
  last_activity_at?: string | null;
  lead_score?: number | null;           // computed lead score 0-100 (issue #18); null = never scored
  lead_score_at?: string | null;
  activity?: CrmActivity[];
}

// Field provenance (issue #16): which standard fields the AI assistant wrote. A row is a
// live badge only while unconfirmed AND not stale (snapshot still equals the live value).
export interface FieldProvenance {
  id: number;
  entity_type: string;
  entity_id: number;
  field_name: string;
  value_snapshot: string | null;
  source: string;
  source_detail: string | null;
  confidence: number | null;
  populated_at: string;
  confirmed_at: string | null;
  stale?: boolean;
}

export interface CrmCompany {
  /** Owner (issue #60). null = unassigned, which is a real state, not missing data.
   *  Ownership is an assignment and a filter, never a permission. */
  owner_id?: number | null;
  id: number;
  name: string;
  domain: string;
  industry: string;
  phone: string;
  address: string;
  notes: string;
  source: string;
  status: string;
  created_at: string;
  updated_at: string;
  // Detail view extras (rolled-up)
  contacts?: CrmContact[];
  deals?: CrmDeal[];
  activity?: CrmActivity[];
  open_deal_value?: number;
}

export interface CrmTask {
  /** Owner (issue #60). null = unassigned, which is a real state, not missing data.
   *  Ownership is an assignment and a filter, never a permission. */
  owner_id?: number | null;
  id: number;
  contact_id: number | null;
  deal_id: number | null;
  contact_name?: string;
  deal_title?: string;
  title: string;
  description: string;
  due_date: string;
  completed: number;
  priority: string;
  created_at: string;
  updated_at: string;
  // GTD column (#70) that rides every `SELECT t.*` response. Normal mode neither shows nor
  // edits it, but the Tasks list reads it for one decision: completing a REPEATING task
  // spawns its next occurrence server-side, a row no local patch can invent, so that one
  // path re-sweeps instead of patching (#77). '' = does not repeat.
  repeat?: string;
}

export interface CrmActivity {
  id: number;
  contact_id: number | null;
  deal_id: number | null;
  contact_name?: string;
  deal_title?: string;
  activity: string;
  note: string;
  created_at: string;
}

/**
 * One file attached to a chatter note (issue #57) — METADATA only.
 *
 * The bytes never travel in a list response: `has_thumb` says whether a server-generated
 * thumbnail exists, and the two byte endpoints are fetched separately, with auth.
 */
export interface CrmAttachment {
  id: number;
  note_id: number;
  filename: string;
  mime_type: string;
  byte_size: number;
  has_thumb: boolean;
  created_at: string;
  uploaded_by: number | null;
}

export interface CrmNote {
  id: number;
  entity_type: string;
  entity_id: number;
  message: string;
  created_at: string;
  updated_at: string | null;
  archived: number;
  // Optional because a note returned by the create/edit endpoints carries no attachments
  // yet — only the list read (`get_chatter`) embeds them.
  attachments?: CrmAttachment[];
}

// Custom fields (issue #19). A definition is the user-authored schema; a value row
// is one definition joined to a specific entity's value (null when unset).
export interface CrmFieldDefinition {
  id: number;
  entity_type: string;              // 'contact' | 'company' | 'deal'
  name: string;
  field_key: string;
  field_type: string;               // 'text' | 'number' | 'boolean' | 'date' | 'select'
  dropdown_options: string[] | null;
  is_required: number;              // 0 | 1 (backend INTEGER flag)
  display_order: number;
  created_at: string;
  updated_at: string;
}

export interface CrmFieldValue {
  field_id: number;
  name: string;
  field_key: string;
  field_type: string;
  dropdown_options: string[] | null;
  is_required: number;
  value: string | null;
  value_updated_at: string | null;
  updated_by_email: string | null;
}

// PUT /{entity}/{id}/fields response.
export interface CrmFieldValuesResult {
  ok: boolean;
  updated: number;
  errors: string[];
}

export interface CrmDashboard {
  total_contacts: number;
  total_companies: number;
  contacts_by_status: Record<string, number>;
  pipeline_by_stage: { stage: string; count: number; total_value: number }[];
  total_pipeline_value: number;
  overdue_tasks: number;
  pending_tasks: number;
  recent_activity: CrmActivity[];
  top_deals: CrmDeal[];
}

// GET /api/crm/dashboard/weekly-touches (issue #76). Open deals touched in a
// window, keyed off #16's AI touch counts. Single-user, so the blueprint's per-rep
// rows are per-deal here. `computed_deals` is the zero-keys gate: 0 means no touch
// count has ever been computed (no AI provider), and the card renders nothing.
export interface CrmWeeklyTouchDeal {
  id: number;
  title: string;
  value: number;
  stage: string;
  touch_count: number | null;
  touched_at: string | null;
  contact_name: string | null;
  company_name: string | null;
}

export interface CrmWeeklyTouches {
  window: { start: string; end: string; label: string; custom: boolean };
  deals: CrmWeeklyTouchDeal[];
  total_touches: number;
  total_open_deals: number;
  computed_deals: number;
}

// GET /api/crm/dashboard/today (issue #130). One ranked list of what needs the viewer
// today, already ordered by the server's priority ladder — the client renders `items`
// in the order given and never re-sorts. Rank 2 is reserved for hot+stale deals (#125),
// which is why the task ranks skip it. Its own response types rather than a widened
// `CrmTask`: a panel row is not a task row, and `CrmTask` declares no `star`.
export interface CrmTodayTaskItem {
  kind: 'task';
  id: number;
  rank: 1 | 3 | 5;
  why: 'starred' | 'overdue' | 'due_today';
  title: string;
  due_date: string;
  owner_id: number | null;
}

export interface CrmTodayReminderItem {
  kind: 'reminder';
  id: string;
  rank: 4;
  why: 'reminder';
  title: string;
  due_at: string;
}

export type CrmTodayItem = CrmTodayTaskItem | CrmTodayReminderItem;

export interface CrmToday {
  /** The SERVER's local day (YYYY-MM-DD). Due labels render against this, not the
   *  browser clock, so a browser in another timezone agrees with the bucketing. */
  date: string;
  /** The next server-local midnight, as an absolute instant. The panel arms its reload
   *  on this rather than on browser midnight — the two can be hours apart. */
  next_refresh_at: string;
  scope: { owner_id: number | null };
  items: CrmTodayItem[];
}

// GET /api/crm/analytics (issue #20). Keyless SQL analytics; win_rate_pct,
// avg_days_to_close, and the avg deal sizes are null when there's no qualifying
// deal (no closed / no won / no open with value) — render as "—", not "$0".
export interface CrmAnalytics {
  window_days: number;
  stale_days: number;
  win_loss: {
    deals_won: number;
    deals_lost: number;
    open_deals: number;
    win_rate_pct: number | null;
    avg_won_deal_size: number | null;
    avg_open_deal_size: number | null;
    avg_days_to_close: number | null;
    total_pipeline_value: number;
  };
  activity: {
    daily: { day: string; count: number }[];
    by_type: { activity: string; count: number }[];
    total: number;
  };
  /** Per-rep rows (issue #60). Pipeline numbers follow OWNERSHIP; activity numbers
   *  follow whoever ACTED, so a rep is credited for work on a colleague's record.
   *  user_id null is the "Unattributed" bucket. Compare reps on records_touched —
   *  activity_count is inflatable by a single bulk action. */
  per_rep: {
    user_id: number | null;
    name: string;
    email: string;
    deals_open: number;
    open_value: number;
    deals_won: number;
    deals_lost: number;
    won_value: number;
    activity_count: number;
    records_touched: number;
  }[];
  aging: {
    buckets: { label: string; min_days: number; max_days: number | null; count: number }[];
    stale_count: number;
    stale_deals: {
      id: number;
      title: string;
      value: number;
      stage: string;
      contact_name: string | null;
      company_name: string | null;
      days_since_touch: number;
      age_days: number;
    }[];
  };
}

// GET /api/crm/deals/:id/touch-count/evidence (issue #56) — the per-event verdicts
// behind the AI touch count. Read-only stored facts; the endpoint never re-runs AI.
export type AiTouchEventState =
  | 'touch' | 'not_touch' | 'not_evaluated' | 'edited_since' | 'excluded_empty' | 'stage_move';

// How current the stored explanation is. 'none' also covers counts written before #56.
export type AiTouchVerdictState = 'current' | 'stale' | 'superseded' | 'none';

export interface AiTouchEvidenceEvent {
  source: 'note' | 'activity' | 'deal_notes' | 'stage_move';
  source_id: number | null;
  event_at: string;
  line: string;
  state: AiTouchEventState;
  reason: string;
}

export interface AiTouchEvidenceResponse {
  deal_id: number;
  open: boolean;
  stage: string;
  ai_touch_count: number | null;
  computed_at: string | null;
  verdict_state: AiTouchVerdictState;
  counted: number | null;
  evaluated: number;
  truncated: boolean;
  events: AiTouchEvidenceEvent[];
}

// GET /api/crm/companies/:id/report (issue #144) — the Reports page's one-company rollup.
// The child lists are capped server-side and say so; the headline numbers in `summary` are
// their own aggregate over the full tables, so no cap and no archive toggle can move them.
// Every expanded row renders entirely from this payload — custom fields and open tasks ride
// it, batched — so opening a row costs no request.

/** One custom field on a rolled-up record: EVERY definition, whether or not it is filled in. */
export interface CrmRollupField {
  field_key: string;
  name: string;
  field_type: string;
  value: string | null;
}

export interface CrmRollupChild {
  activities: CrmActivity[];
  activities_truncated: boolean;
  custom_fields: CrmRollupField[];
}

export interface CrmRollupSummary {
  open_deal_count: number;
  open_deal_value: number;
  /**
   * The one currency every open deal agrees on, or null when they disagree (and when
   * there are no open deals). `deals.currency` is user-writable, so a sum across
   * currencies is a false number — null means "do not render this as one figure".
   */
  open_deal_currency: string | null;
  /** `status = 'active'` only: BOTH inactive and archived are excluded, because the chip
   *  this feeds says "Active contacts". The contacts LIST below is unfiltered. */
  contact_count: number;
}

export interface CrmCompanyRollup {
  company: CrmCompany;
  company_custom_fields: CrmRollupField[];
  summary: CrmRollupSummary;
  contacts: (CrmContact & CrmRollupChild)[];
  deals: (CrmDeal & CrmRollupChild & {
    last_activity_at: string | null;
    tasks: CrmTask[];
    tasks_truncated: boolean;
  })[];
  contacts_truncated: boolean;
  deals_truncated: boolean;
}

/**
 * One row of the merged company feed (GET /api/crm/companies/:id/timeline).
 *
 * `source` says which table the row came from. It is not decoration: notes live in
 * `crm_chatter` and activities in `activity_log`, two tables with INDEPENDENT id sequences,
 * so `id` alone repeats across the feed. `(created_at, source, id)` is the server's total
 * order, and `${source}:${id}` is the only safe React key or dedupe key.
 */
export interface CrmTimelineEntry {
  source: 'note' | 'activity';
  id: number;
  entity_type: 'company' | 'contact' | 'deal';
  entity_id: number;
  /** The activity kind ("call", "email", …). Null for a note. */
  activity: string | null;
  /** A note's text, or an activity's note ('' when it carries none). */
  message: string;
  created_at: string;
  /** Notes only: set on edit, null when never edited. Always null for an activity. */
  updated_at: string | null;
  /** 0/1 for notes; always 0 for activities, which have no archived concept. */
  archived: number;
  /** Note author / activity actor. Null = unattributed (the assistant's own writes). */
  actor_id: number | null;
  /** The parent record's display name, hydrated per page. */
  source_name: string;
  source_archived: boolean;
  /** Notes only. */
  attachments?: CrmAttachment[];
}

export interface CrmTimelinePage {
  entries: CrmTimelineEntry[];
  has_more: boolean;
}
