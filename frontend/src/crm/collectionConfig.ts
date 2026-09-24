/**
 * Collection-layer configuration for the three CRM list pages (issue #77).
 *
 * All the DATA of a list surface — what is searched, what is sortable, which facets exist
 * and what each one selects — lives here as plain values, JSX-free and unit-testable in a
 * node environment. Anything that renders (columns, the one custom facet's control and
 * chip) lives in `listColumns.tsx` and is passed in as a dep.
 *
 * Two conventions the layer imposes, both load-bearing:
 *
 * - **A sort getter returns `null`, never `0` or `''`, for a missing value.** `shared/search`
 *   sorts nulls to the bottom in BOTH directions; a `0` is a real number and would sort a
 *   blank straight to the top ascending.
 * - **A config object must be referentially stable.** These factories are called from a
 *   `useMemo` (or module scope where they take no runtime deps); a fresh object per render
 *   re-derives the search documents for the entire corpus on every keystroke.
 */
import type {
  CollectionConfig,
  CustomFacetDef,
  FacetDef,
  MultiFacetDef,
} from '../shared/collection';
import type { FacetOption, SortFieldDef, SortState } from '../shared/search';
import type { ListColumn } from '../shared/listview';
import type { CrmCompany, CrmContact, CrmTodo } from '../core/types';
import type { CrmUser } from './useUsers';
import { UNASSIGNED_LABEL } from './useUsers';
import { scoreBand, type ScoreBand } from './constants';
import { matchesActivityPreset, ymd, type ActivityPreset } from './pipelineFilters';
import { parseTags, parseUTC } from './gtd/util';

/** Sortable timestamp: epoch millis, or null when absent/unparseable (never 0 — see header). */
function isoMillis(ts: string | null | undefined): number | null {
  if (!ts) return null;
  const t = parseUTC(ts).getTime();
  return Number.isNaN(t) ? null : t;
}

/** Sortable text: lowercased, or null when blank. */
function text(value: string | null | undefined): string | null {
  const v = value?.trim().toLowerCase();
  return v ? v : null;
}

// ── Owner facet (shared by all three surfaces) ──────────────────────────────

/** `owner_id` is absent on a pre-#60 payload and null when unassigned; both mean nobody. */
const OWNER_UNASSIGNED = 'unassigned';

/**
 * Options for the Owner facet, or `null` on a single-seat install — where every record has
 * the same owner and the facet is pure noise. `null` means "declare no Owner facet at all",
 * which is exactly what `OwnerScopeToggle` did by hiding itself under `users.length <= 1`.
 *
 * Deactivated users stay listed: they still own records, and a filter that cannot name them
 * cannot find that work.
 */
export function buildOwnerOptions(users: CrmUser[], meId: number | null): FacetOption[] | null {
  if (users.length <= 1) return null;
  return [
    { value: OWNER_UNASSIGNED, label: UNASSIGNED_LABEL },
    ...users.map(u => ({
      value: u.id,
      label: (u.name.trim() || u.email)
        + (u.id === meId ? ' (me)' : '')
        + (u.is_active ? '' : ' (deactivated)'),
    })),
  ];
}

function ownerFacet<T extends { owner_id?: number | null }>(
  options: FacetOption[],
): MultiFacetDef<T> {
  return {
    key: 'owner',
    label: 'Owner',
    getValue: r => r.owner_id ?? OWNER_UNASSIGNED,
    options,
  };
}

// ── Contacts ────────────────────────────────────────────────────────────────

export const CONTACT_SORT_FIELDS = [
  { value: 'updated_at', label: 'Recently updated', get: (c: CrmContact) => isoMillis(c.updated_at) },
  { value: 'name', label: 'Name', get: (c: CrmContact) => text(c.name) },
  { value: 'company', label: 'Company', get: (c: CrmContact) => text(c.company_name || c.company) },
  { value: 'email', label: 'Email', get: (c: CrmContact) => text(c.email) },
  { value: 'lead_score', label: 'Score', get: (c: CrmContact) => c.lead_score ?? null },
  { value: 'last_contact', label: 'Last contact', get: (c: CrmContact) => isoMillis(c.last_contact_at) },
  { value: 'status', label: 'Status', get: (c: CrmContact) => text(c.status) },
] as const satisfies readonly SortFieldDef<CrmContact>[];

/** The order the server used to return by default — kept so the page opens the same way. */
export const CONTACT_DEFAULT_SORT: SortState = { field: 'updated_at', dir: 'desc' };

export const SCORE_OPTIONS: FacetOption[] = [
  { value: 'hot', label: 'Hot (70+)' },
  { value: 'warm', label: 'Warm (40-69)' },
  { value: 'cool', label: 'Cool (under 40)' },
  { value: 'unscored', label: 'Not scored' },
];

export const LAST_CONTACT_OPTIONS: FacetOption[] = [
  { value: 'le7', label: 'Last 7 days' },
  { value: 'le30', label: 'Last 30 days' },
  { value: 'stale30', label: 'Not in 30 days' },
  { value: 'none', label: 'Never' },
];

/** True when `score` falls in the band named by a SCORE_OPTIONS value. */
export function matchesScoreBand(score: number | null | undefined, band: string | number): boolean {
  if (band === 'unscored') return score == null;
  return score != null && scoreBand(score) === (band as ScoreBand);
}

export interface ContactsConfigDeps {
  columns: ListColumn<CrmContact>[];
  /** null ⇒ single-seat install, no Owner facet. */
  owners: FacetOption[] | null;
  /** The viewer's current local day (see useLocalDay) — the recency buckets pivot on it. */
  now: Date;
}

export function makeContactsCollectionConfig(deps: ContactsConfigDeps): CollectionConfig<CrmContact> {
  const facets: FacetDef<CrmContact>[] = [
    { key: 'status', label: 'Status', getValue: c => c.status || null },
    // `?? ''` is not defensive noise: the layer calls getValue for EVERY row to derive the
    // option list, so one row missing the field would throw inside a render and take the
    // whole page down rather than just that facet.
    { key: 'tags', label: 'Tags', getValue: c => parseTags(c.tags ?? ''), searchable: true },
    ...(deps.owners ? [ownerFacet<CrmContact>(deps.owners)] : []),
    {
      kind: 'single', key: 'score', label: 'Score',
      options: SCORE_OPTIONS,
      predicate: (c, v) => matchesScoreBand(c.lead_score, v),
    },
    {
      kind: 'single', key: 'lastContact', label: 'Last contact',
      options: LAST_CONTACT_OPTIONS,
      // `now` is passed in rather than read here, because a predicate only runs when
      // React re-renders — see useLocalDay. Taking it as a parameter also makes the
      // config's day-dependence explicit, which is what advances it at midnight.
      predicate: (c, v) => matchesActivityPreset(c.last_contact_at, v as ActivityPreset, deps.now),
    },
  ];
  return {
    storage: { key: 'crm_contacts', version: 1 },
    defaultView: 'list',
    getItemId: c => c.id,
    // Mirrors what the retired server-side `q=` matched (name/email/company/notes), plus the
    // fields a client-side index can afford: phone, title, tags. Search is recall.
    searchText: c => [c.name, c.email, c.phone, c.company_name, c.company, c.title, c.tags, c.notes],
    persistSearch: true,
    facets,
    sort: { fields: CONTACT_SORT_FIELDS, defaultSort: CONTACT_DEFAULT_SORT },
    list: { columns: deps.columns },
    itemNoun: { singular: 'contact', plural: 'contacts' },
    // Neutral wording: the layer shows this both for an empty corpus and for a filtered-out
    // table, so "Add your first one!" would be wrong half the time.
    emptyState: { message: 'No contacts to show.' },
  };
}

// ── Companies ───────────────────────────────────────────────────────────────

export const COMPANY_SORT_FIELDS = [
  { value: 'name', label: 'Name', get: (c: CrmCompany) => text(c.name) },
  { value: 'industry', label: 'Industry', get: (c: CrmCompany) => text(c.industry) },
  { value: 'domain', label: 'Domain', get: (c: CrmCompany) => text(c.domain) },
  { value: 'status', label: 'Status', get: (c: CrmCompany) => text(c.status) },
  { value: 'updated_at', label: 'Recently updated', get: (c: CrmCompany) => isoMillis(c.updated_at) },
] as const satisfies readonly SortFieldDef<CrmCompany>[];

export const COMPANY_DEFAULT_SORT: SortState = { field: 'name', dir: 'asc' };

export interface CompaniesConfigDeps {
  columns: ListColumn<CrmCompany>[];
  owners: FacetOption[] | null;
}

export function makeCompaniesCollectionConfig(deps: CompaniesConfigDeps): CollectionConfig<CrmCompany> {
  const facets: FacetDef<CrmCompany>[] = [
    { key: 'status', label: 'Status', getValue: c => c.status || null },
    { key: 'industry', label: 'Industry', getValue: c => c.industry || null, searchable: true },
    ...(deps.owners ? [ownerFacet<CrmCompany>(deps.owners)] : []),
  ];
  return {
    storage: { key: 'crm_companies', version: 1 },
    defaultView: 'list',
    getItemId: c => c.id,
    searchText: c => [c.name, c.domain, c.industry, c.phone, c.address, c.notes],
    persistSearch: true,
    facets,
    sort: { fields: COMPANY_SORT_FIELDS, defaultSort: COMPANY_DEFAULT_SORT },
    list: { columns: deps.columns },
    itemNoun: { singular: 'company', plural: 'companies' },
    emptyState: { message: 'No companies to show.' },
  };
}

// ── Todos ───────────────────────────────────────────────────────────────────

/** Matches the backend's advisory priority vocabulary; ordered by urgency, not alphabet. */
export const TODO_PRIORITY_OPTIONS: FacetOption[] = [
  { value: 'high', label: 'High' },
  { value: 'medium', label: 'Medium' },
  { value: 'low', label: 'Low' },
];

const PRIORITY_RANK: Record<string, number> = { high: 3, medium: 2, low: 1 };

export const TODO_SORT_FIELDS = [
  // The server's historical order (`completed ASC, due_date ASC`) as ONE getter, because
  // the layer sorts by a single value per field. Without it the "All" view interleaves
  // done and open todos, which the old tab bar never did. '~' sorts after every digit in
  // ASCII, so an undated todo lands last WITHIN its group rather than last overall.
  {
    value: 'open_due', label: 'Open first, then due',
    get: (t: CrmTodo) => `${t.completed ? 1 : 0}|${t.due_date || '~'}`,
  },
  // due_date is a date-only string, so lexicographic order IS chronological; '' (unset)
  // maps to null and therefore sorts LAST in both directions — an improvement on the
  // server order, which put undated todos first.
  { value: 'due', label: 'Due date', get: (t: CrmTodo) => t.due_date || null },
  { value: 'priority', label: 'Priority', get: (t: CrmTodo) => PRIORITY_RANK[t.priority] ?? null },
  { value: 'title', label: 'Title', get: (t: CrmTodo) => text(t.title) },
  { value: 'created_at', label: 'Recently added', get: (t: CrmTodo) => isoMillis(t.created_at) },
] as const satisfies readonly SortFieldDef<CrmTodo>[];

export const TODO_DEFAULT_SORT: SortState = { field: 'open_due', dir: 'asc' };

export type DuePreset = 'overdue' | 'today' | 'next7' | 'none';

export const TODO_DUE_OPTIONS: FacetOption[] = [
  { value: 'overdue', label: 'Overdue' },
  { value: 'today', label: 'Due today' },
  { value: 'next7', label: 'Next 7 days' },
  { value: 'none', label: 'No due date' },
];

/**
 * Due-date bucket, on the VIEWER'S LOCAL calendar day via `ymd`.
 *
 * The page this replaces derived "today" from `toISOString()`, i.e. the UTC day, so west of
 * Greenwich every evening a todo due tomorrow already read as due today.
 */
export function matchesDuePreset(todo: CrmTodo, preset: DuePreset, now: Date): boolean {
  const due = todo.due_date || '';
  switch (preset) {
    case 'none':
      return !due;
    case 'today':
      return due === ymd(now);
    case 'next7':
      return !!due && due >= ymd(now) && due <= ymd(now, 7);
    case 'overdue':
      // Only an OPEN todo can be overdue — a completed one was dealt with, late or not.
      return !todo.completed && !!due && due < ymd(now);
  }
}

/** Which todos the list shows at rest. `open` reproduces today's default "Pending" tab. */
export type DonePreset = 'open' | 'done' | 'all';

export const DONE_OPTIONS: FacetOption[] = [
  { value: 'open', label: 'Open' },
  { value: 'done', label: 'Done' },
  { value: 'all', label: 'All' },
];

export function coerceDonePreset(raw: unknown): DonePreset {
  return raw === 'done' || raw === 'all' ? raw : 'open';
}

export function matchesDonePreset(todo: CrmTodo, value: DonePreset): boolean {
  if (value === 'all') return true;
  return value === 'done' ? !!todo.completed : !todo.completed;
}

/** The rendering half of the Done facet, supplied by `listColumns.tsx` (it returns JSX). */
export type DoneFacetRenderers = Pick<
  CustomFacetDef<CrmTodo, DonePreset>, 'renderControl' | 'renderChip'
>;

export interface TodosConfigDeps {
  columns: ListColumn<CrmTodo>[];
  owners: FacetOption[] | null;
  doneFacet: DoneFacetRenderers;
  /** The viewer's current local day (see useLocalDay) — the due buckets pivot on it. */
  now: Date;
}

export function makeTodosCollectionConfig(deps: TodosConfigDeps): CollectionConfig<CrmTodo> {
  // Open/Done/All is a CUSTOM facet rather than a toggle or a single-select, and the reason
  // is narrow: only `CustomFacetDef` carries a `defaultValue`, and this facet has to default
  // to an ACTIVE state ("Open") to reproduce today's Pending-by-default page. A toggle would
  // also have been wrong twice over — the layer never filters rows on toggles (they are
  // documented as column visibility), and a two-state control cannot express the Done-only
  // view the old tab bar had.
  const done: CustomFacetDef<CrmTodo, DonePreset> = {
    kind: 'custom',
    key: 'done',
    label: 'Show',
    defaultValue: 'open',
    isActive: v => v !== 'all',
    coerce: coerceDonePreset,
    predicate: matchesDonePreset,
    renderControl: deps.doneFacet.renderControl,
    renderChip: deps.doneFacet.renderChip,
  };
  const facets: FacetDef<CrmTodo>[] = [
    done,
    {
      kind: 'single', key: 'due', label: 'Due',
      options: TODO_DUE_OPTIONS,
      predicate: (t, v) => matchesDuePreset(t, v as DuePreset, deps.now),
    },
    { key: 'priority', label: 'Priority', getValue: t => t.priority || null, options: TODO_PRIORITY_OPTIONS },
    ...(deps.owners ? [ownerFacet<CrmTodo>(deps.owners)] : []),
  ];
  return {
    storage: { key: 'crm_todos', version: 1 },
    defaultView: 'list',
    getItemId: t => t.id,
    searchText: t => [t.title, t.description, t.contact_name, t.deal_title],
    persistSearch: true,
    facets,
    sort: { fields: TODO_SORT_FIELDS, defaultSort: TODO_DEFAULT_SORT },
    list: { columns: deps.columns },
    detail: {
      getTitle: t => t.title,
      getSubtitle: t => [t.contact_name, t.deal_title].filter(Boolean).join(' · ') || null,
    },
    itemNoun: { singular: 'todo', plural: 'todos' },
    emptyState: { message: 'No todos to show.' },
  };
}
