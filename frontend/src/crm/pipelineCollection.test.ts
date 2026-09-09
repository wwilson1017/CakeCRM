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
import type { FacetDef, MultiFacetDef, SingleFacetDef, BooleanFacetDef, RangeFacetDef } from '../shared/collection';
import type { CrmUser } from './useUsers';
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

  it('declares NO detail — the deal sheet stays a page-owned modal until #75', () => {
    expect(build().detail).toBeUndefined();
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
