/**
 * Pins the pipeline's collection config. Three of these assertions guard invariants that are
 * invisible at the call site and would fail silently if broken:
 *   • `getVoided` absent — the precondition that makes kanbanItems === visibleItems, which is
 *     why the bulk bar's count and the apply payload describe the same deals.
 *   • the owner facet declared even with an empty roster — a facet that appears LATER has its
 *     persisted selection erased, because useCollectionState coerces its envelope once.
 *   • `dragPolicy: 'column'` — what keeps drag alive while filtering (#21's shipped behavior).
 */
import { describe, expect, it } from 'vitest';
import type { CrmDeal } from '../core/types';
import type { FacetDef, MultiFacetDef, SingleFacetDef, BooleanFacetDef, RangeFacetDef, CustomFacetDef, DateRangeValue } from '../shared/collection';
import type { CrmUser } from './useUsers';
import { ymd } from './pipelineFilters';
import { DEAL_DETAIL_CONFIG } from './dealDetailConfig';
import { archivedSelectionIncludesArchived, makePipelineCollectionConfig } from './pipelineCollection';
import { isManualSort } from '../shared/search';
import { PIPELINE_DEFAULT_SORT, pipelineSortFields } from './pipelineSort';

function deal(over: Partial<CrmDeal> & { id: number }): CrmDeal {
  return {
    contact_id: null, company_id: null, title: `Deal ${over.id}`, stage: 'lead',
    value: 0, notes: '', expected_close_date: '', probability: 0, currency: 'USD',
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    ...over,
  };
}

const user = (id: number, name: string, is_active = true): CrmUser => ({
  id, name, email: `${name.toLowerCase()}@example.com`, role: 'member', is_active,
});

const build = (users: CrmUser[] = []) =>
  makePipelineCollectionConfig({ users, ownerName: id => `User ${id}`, listColumns: [] });

const facet = <T extends FacetDef<CrmDeal>>(key: string): T => {
  const found = build([user(1, 'Ada')]).facets?.find(f => f.key === key);
  if (!found) throw new Error(`facet ${key} not declared`);
  return found as T;
};

describe('load-bearing absences', () => {
  it('declares NO getVoided — kanbanItems and visibleItems must stay the same array', () => {
    expect(build().getVoided).toBeUndefined();
  });

  // Inverted by #75: the deal detail is the layer's now. `CollectionView` mounts
  // `CollectionDetail` only when `detail && config.detail` are BOTH present, so this half of the
  // gate is what makes the panel exist at all — and it must be the SHARED object, or the pipeline
  // and the dashboard drift about the title, the subtitle and the `loadById` route.
  it('declares the SHARED deal detail config, so the three hosts cannot drift', () => {
    expect(build().detail).toBe(DEAL_DETAIL_CONFIG.detail);
    expect(build().detail?.getTitle(deal({ id: 1, title: 'Rebuild' }))).toBe('Rebuild');
  });
});

describe('owner facet', () => {
  it('is declared even with an EMPTY roster, so a persisted selection is never coerced away', () => {
    const owner = build([]).facets?.find(f => f.key === 'owner');
    expect(owner).toBeDefined();
  });

  it('always leads with the Unassigned bucket and appends the roster', () => {
    const owner = facet<MultiFacetDef<CrmDeal>>('owner');
    expect(owner.options?.map(o => o.value)).toEqual(['unassigned', 1]);
  });

  it('marks a deactivated user rather than hiding them', () => {
    const cfg = makePipelineCollectionConfig({
      users: [user(2, 'Bo', false)], ownerName: id => `User ${id}`, listColumns: [],
    });
    const owner = cfg.facets?.find(f => f.key === 'owner') as MultiFacetDef<CrmDeal>;
    expect(owner.options?.[1].label).toBe('Bo (deactivated)');
  });

  it('buckets an absent owner_id with an explicit null', () => {
    const owner = facet<MultiFacetDef<CrmDeal>>('owner');
    expect(owner.getValue(deal({ id: 1 }))).toBe('unassigned');
    expect(owner.getValue(deal({ id: 2, owner_id: null }))).toBe('unassigned');
    expect(owner.getValue(deal({ id: 3, owner_id: 5 }))).toBe(5);
  });
});

describe('stage facet', () => {
  it('offers every stage in STAGE_ORDER with its theme colour', () => {
    const stage = facet<MultiFacetDef<CrmDeal>>('stage');
    expect(stage.options?.map(o => o.value)).toEqual(['lead', 'qualified', 'proposal', 'negotiation', 'won', 'lost']);
    expect(stage.options?.[0].color).toContain('--color-ck-stage-lead');
  });
});

describe('value facet', () => {
  it('reads a missing value as 0 so a range never silently excludes it', () => {
    const value = facet<RangeFacetDef<CrmDeal>>('value');
    expect(value.getValue(deal({ id: 1, value: undefined as unknown as number }))).toBe(0);
    expect(value.getValue(deal({ id: 2, value: 250 }))).toBe(250);
  });
});

describe('preset facets delegate to the #21 predicate', () => {
  it("closeDate 'overdue' stays open-deals-only through the delegation", () => {
    const close = facet<SingleFacetDef<CrmDeal>>('closeDate');
    const past = '2020-01-01';
    expect(close.predicate(deal({ id: 1, expected_close_date: past, stage: 'proposal' }), 'overdue')).toBe(true);
    expect(close.predicate(deal({ id: 2, expected_close_date: past, stage: 'won' }), 'overdue')).toBe(false);
  });

  it("lastActivity 'none' matches a deal with no activity", () => {
    const activity = facet<SingleFacetDef<CrmDeal>>('lastActivity');
    expect(activity.predicate(deal({ id: 1, last_activity_at: null }), 'none')).toBe(true);
    expect(activity.predicate(deal({ id: 2, last_activity_at: new Date().toISOString() }), 'none')).toBe(false);
  });

  it('has no boolean facets — stage visibility rides the toggle channel, not a filter', () => {
    const booleans = build().facets?.filter((f): f is BooleanFacetDef<CrmDeal> => f.kind === 'boolean');
    expect(booleans).toEqual([]);
  });
});

describe('board wiring', () => {
  it("uses dragPolicy 'column' so a filter cannot pause drag", () => {
    expect(build().kanban?.dragPolicy).toBe('column');
  });

  it('renders every card in a column rather than capping at the layer default', () => {
    expect(build().kanban?.columnCap).toBe(Number.MAX_SAFE_INTEGER);
  });

  it('groups the board by stage', () => {
    expect(build().kanban?.getColumnId(deal({ id: 1, stage: 'proposal' }))).toBe('proposal');
  });

  it('declares one visibility toggle per stage', () => {
    expect(build().toggles?.map(t => t.key)).toEqual([
      'stage:lead', 'stage:qualified', 'stage:proposal', 'stage:negotiation', 'stage:won', 'stage:lost',
    ]);
    // Default visible: a fresh session shows the whole board.
    expect(build().toggles?.every(t => t.default === true)).toBe(true);
  });
});

describe('search and persistence', () => {
  it('searches exactly the pre-#74 haystack', () => {
    const d = deal({ id: 1, title: 'Big', contact_name: 'Ada', company_name: 'Acme' });
    expect(build().searchText(d)).toEqual(['Big', 'Ada', 'Acme']);
  });

  it('persists the query, the board default and the storage key', () => {
    const cfg = build();
    expect(cfg.persistSearch).toBe(true);
    expect(cfg.defaultView).toBe('kanban');
    expect(cfg.storage).toEqual({ key: 'crm_pipeline', version: 1 });
  });

  it('rests on the arrayOrder sort, which is what keeps the list "natural" at rest', () => {
    expect(isManualSort(PIPELINE_DEFAULT_SORT, pipelineSortFields(() => ''))).toBe(true);
    expect(build().sort?.defaultSort).toEqual(PIPELINE_DEFAULT_SORT);
  });
});

// ── The Archived facet (issue #83, re-expressed on the layer by #74) ──────────
// #83 shipped this as an `archived` key on `AdvancedFilters` plus a hand-rolled
// sessionStorage coercion. #74 retired both — the value lives in the layer's envelope now —
// so the assertions that guarded it move here, to the surface that owns the rule. Two
// contracts are load-bearing and neither is visible at the call site: the facet is what
// makes archived deals REACHABLE, and it is also the only facet that widens the FETCH.
describe('archived facet', () => {
  const live = deal({ id: 1 });
  const archived = deal({ id: 2, archived_at: '2026-08-20T00:00:00+00:00' });
  const archivedFacet = () => facet<SingleFacetDef<CrmDeal>>('archived');

  it('offers exactly the two states that widen the fetch', () => {
    expect(archivedFacet().options.map(o => o.value)).toEqual(['include', 'only']);
  });

  it("passes both under 'include'", () => {
    const f = archivedFacet();
    expect(f.predicate(live, 'include')).toBe(true);
    expect(f.predicate(archived, 'include')).toBe(true);
  });

  it("passes archived deals ONLY under 'only' — the recovery view", () => {
    const f = archivedFacet();
    expect(f.predicate(live, 'only')).toBe(false);
    expect(f.predicate(archived, 'only')).toBe(true);
  });

  it('fails an unrecognised value toward LIVE-ONLY, never toward a wider board', () => {
    // The layer's scalar coercion accepts any string, so a hand-edited or legacy envelope can
    // carry one. Widening the board on junk would show archived deals to a session that never
    // asked — and `archivedSelectionIncludesArchived` keeps the FETCH narrow for the same value,
    // so the two halves agree.
    expect(archivedFacet().predicate(archived, 'bogus')).toBe(false);
    expect(archivedFacet().predicate(live, 'bogus')).toBe(true);
    expect(archivedSelectionIncludesArchived('bogus')).toBe(false);
  });

  it('is a plain single facet, so the layer counts it and Clear filters resets it', () => {
    // #83 counted `archived` in `advancedActiveCount` and cleared it with the other facets.
    // On the layer both fall out of `kind: 'single'` — `selectionActive` counts any non-null
    // selection and `clearFacets` restores `defaultSelection`, which for 'single' is null.
    expect(archivedFacet().kind).toBe('single');
  });

  it('widens the fetch for exactly the two real selections and nothing else', () => {
    expect(archivedSelectionIncludesArchived('include')).toBe(true);
    expect(archivedSelectionIncludesArchived('only')).toBe(true);
    expect(archivedSelectionIncludesArchived(null)).toBe(false);
    expect(archivedSelectionIncludesArchived(undefined)).toBe(false);
  });
});

describe('date facets (#181)', () => {
  const range = (key: string) => facet<CustomFacetDef<CrmDeal, DateRangeValue>>(key);
  const q1 = { from: '2026-01-01', to: '2026-03-31' };

  it('declares each range beside its preset, in panel order', () => {
    // Declared order IS panel order, so a range sits under the preset it extends rather than
    // at the bottom of the panel away from it.
    expect(build().facets?.map(f => f.key)).toEqual([
      'stage',
      'owner',
      'value',
      'closeDate',
      'closeDateRange',
      'closedOn',
      'createdDate',
      'createdDateRange',
      'lastActivity',
      'lastActivityRange',
      'archived',
    ]);
  });

  it('keeps the pipeline at storage version 1 — adding facets is not a shape change', () => {
    // coerceSelections walks the DECLARED facets and defaults a key the envelope lacks, so a
    // session saved before #181 restores cleanly. The version pins facet KEYS, and saved views
    // are stamped with it.
    expect(build().storage).toEqual({ key: 'crm_pipeline', version: 1 });
  });

  it('the Created preset delegates to dealMatchesAdvanced like the other two', () => {
    const created = facet<SingleFacetDef<CrmDeal>>('createdDate');
    expect(created.kind).toBe('single');
    expect(created.options.map(o => o.value)).toEqual([
      'last7', 'last30', 'thisMonth', 'thisQuarter', 'lastQuarter',
    ]);
    const now = new Date();
    const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1, 12);
    expect(created.predicate(deal({ id: 1, created_at: yesterday.toISOString() }), 'last7')).toBe(true);
    expect(created.predicate(deal({ id: 2, created_at: '2020-01-01T00:00:00Z' }), 'last7')).toBe(false);
  });

  it('the three ranges are custom facets, so the layer needed no new kind', () => {
    for (const key of ['closeDateRange', 'createdDateRange', 'lastActivityRange']) {
      expect(range(key).kind).toBe('custom');
      expect(range(key).isActive(range(key).defaultValue)).toBe(false);
    }
  });

  it('closeDateRange reads the date-only close field verbatim', () => {
    const f = range('closeDateRange');
    expect(f.predicate(deal({ id: 1, expected_close_date: '2026-03-15' }), q1)).toBe(true);
    expect(f.predicate(deal({ id: 2, expected_close_date: '2026-04-01' }), q1)).toBe(false);
    // A deal with no close date is what the preset's "No close date" bucket is for.
    expect(f.predicate(deal({ id: 3, expected_close_date: '' }), q1)).toBe(false);
  });

  it('createdDateRange reads created_at as a local calendar day', () => {
    const f = range('createdDateRange');
    expect(f.predicate(deal({ id: 1, created_at: '2026-02-02T18:00:00Z' }), q1)).toBe(true);
    expect(f.predicate(deal({ id: 2, created_at: '2026-06-02T18:00:00Z' }), q1)).toBe(false);
    // 03:30 UTC on 1 Apr is still 31 Mar in America/Chicago, which the runner pins.
    expect(f.predicate(deal({ id: 3, created_at: '2026-04-01T03:30:00Z' }), q1)).toBe(true);
  });

  it('lastActivityRange reads last_activity_at and never matches a deal with none', () => {
    const f = range('lastActivityRange');
    expect(f.predicate(deal({ id: 1, last_activity_at: '2026-02-02T18:00:00Z' }), q1)).toBe(true);
    expect(f.predicate(deal({ id: 2, last_activity_at: null }), q1)).toBe(false);
    expect(f.predicate(deal({ id: 3 }), q1)).toBe(false);
  });

  it('a range coerces junk to inactive rather than throwing inside the state initialiser', () => {
    const f = range('closeDateRange');
    expect(f.coerce(null)).toEqual({ from: null, to: null });
    expect(f.coerce({ from: '2026-13-45' })).toEqual({ from: null, to: null });
    expect(f.coerce({ from: '2026-01-01', to: 'soon' })).toEqual({ from: '2026-01-01', to: null });
  });

  it('rests inactive, so the layer never consults its predicate and nothing is filtered', () => {
    // `applyFacets` only evaluates facets `selectionActive` reports as active. That gate is
    // what makes it safe for the predicate to answer false for a dateless deal: at rest the
    // predicate is never reached, and a dateless deal is only excluded once a bound is set.
    const f = range('closeDateRange');
    expect(f.isActive(f.coerce(undefined))).toBe(false);
    expect(f.isActive({ from: '2026-01-01', to: null })).toBe(true);
  });

  it('a range and its preset stay separate facets, so the layer ANDs them', () => {
    // Merging them would have meant a new value shape and a rewritten preset predicate; two
    // facets compose for free and leave every #21 rule byte-identical.
    const preset = facet<SingleFacetDef<CrmDeal>>('closeDate');
    const march = deal({ id: 1, expected_close_date: '2026-03-15', stage: 'lead' });
    expect(range('closeDateRange').predicate(march, q1)).toBe(true);
    expect(preset.predicate(march, 'noDate')).toBe(false);
  });
});

describe('the closedOn facet (#279)', () => {
  it('is a single-choice facet over the Closed on buckets that delegates to the shared predicate', () => {
    const f = facet<SingleFacetDef<CrmDeal>>('closedOn');
    expect(f.kind).toBe('single');
    expect(f.label).toBe('Closed on');
    expect(f.options.map(o => o.value)).toEqual(['last7', 'thisMonth', 'lastMonth', 'noDate']);
    const today = ymd(new Date());
    expect(f.predicate(deal({ id: 1, stage: 'won', closed_on: today }), 'last7')).toBe(true);
    expect(f.predicate(deal({ id: 2, stage: 'won', closed_on: null }), 'noDate')).toBe(true);
    expect(f.predicate(deal({ id: 3, stage: 'lead' }), 'noDate')).toBe(false);
  });

  it('adds no storage bump — the pipeline is still version 1', () => {
    expect(build().storage).toEqual({ key: 'crm_pipeline', version: 1 });
  });
});
