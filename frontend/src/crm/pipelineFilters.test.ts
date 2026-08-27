// @vitest-environment jsdom
//
// jsdom only for `sessionStorage` (loadFilterState/saveFilterState); the predicate itself
// is pure. What this pins is the Archived facet's contract (issue #83): live-only is the
// DEFAULT — including for every session persisted before the facet existed — and the
// tri-state predicate stays authoritative client-side even though the same rule is
// enforced by the server, because the two are briefly out of step every time the facet
// changes.
import { beforeEach, describe, expect, it } from 'vitest';

import type { CrmDeal } from '../core/types';
import {
  EMPTY_ADVANCED,
  advancedActiveCount,
  dealMatchesAdvanced,
  hasAdvanced,
  isArchivedDeal,
  loadFilterState,
  saveFilterState,
  type AdvancedFilters,
} from './pipelineFilters';

const NOW = new Date('2026-08-26T12:00:00Z');

function deal(over: Partial<CrmDeal> = {}): CrmDeal {
  return {
    id: 1, title: 'Wholesale order', stage: 'lead', value: 1000, probability: 20,
    expected_close_date: '', notes: '', contact_id: null, company_id: null, currency: 'USD',
    created_at: '2026-08-01T00:00:00+00:00', updated_at: '2026-08-01T00:00:00+00:00',
    ...over,
  } as CrmDeal;
}

const live = deal({ id: 1 });
const archived = deal({ id: 2, archived_at: '2026-08-20T00:00:00+00:00' });

function advanced(over: Partial<AdvancedFilters> = {}): AdvancedFilters {
  return { ...EMPTY_ADVANCED, ...over };
}

describe('isArchivedDeal', () => {
  it('reads a timestamp as archived and null/absent as live', () => {
    expect(isArchivedDeal(archived)).toBe(true);
    expect(isArchivedDeal(deal({ archived_at: null }))).toBe(false);
    expect(isArchivedDeal(deal())).toBe(false);
  });
});

describe('the archived facet', () => {
  it('excludes archived deals by default', () => {
    // The default is what a board renders in the window after the facet is CLEARED but
    // before the narrowing refetch lands — archived rows are still in state, and this is
    // the only thing hiding them.
    expect(dealMatchesAdvanced(live, advanced(), NOW)).toBe(true);
    expect(dealMatchesAdvanced(archived, advanced(), NOW)).toBe(false);
  });

  it("passes both under 'include'", () => {
    const f = advanced({ archived: 'include' });
    expect(dealMatchesAdvanced(live, f, NOW)).toBe(true);
    expect(dealMatchesAdvanced(archived, f, NOW)).toBe(true);
  });

  it("passes archived deals ONLY under 'only' — the recovery view", () => {
    const f = advanced({ archived: 'only' });
    expect(dealMatchesAdvanced(live, f, NOW)).toBe(false);
    expect(dealMatchesAdvanced(archived, f, NOW)).toBe(true);
  });

  it('still ANDs with the other facets rather than short-circuiting them', () => {
    const f = advanced({ archived: 'include', stages: ['won'] });
    expect(dealMatchesAdvanced(archived, f, NOW)).toBe(false);
    expect(dealMatchesAdvanced(deal({ ...archived, stage: 'won' }), f, NOW)).toBe(true);
  });

  it('counts as an active facet, so the board knows it is filtering', () => {
    expect(advancedActiveCount(advanced())).toBe(0);
    expect(advancedActiveCount(advanced({ archived: 'only' }))).toBe(1);
    expect(hasAdvanced(advanced({ archived: 'include' }))).toBe(true);
  });
});

describe('persistence', () => {
  beforeEach(() => sessionStorage.clear());

  it('defaults to live-only', () => {
    expect(EMPTY_ADVANCED.archived).toBeNull();
    expect(loadFilterState().advanced.archived).toBeNull();
  });

  it('restores a pre-#83 blob with no `archived` key as live-only', () => {
    // The upgrade path. A resumed session must never silently widen the fetch to a
    // setting the user never chose.
    sessionStorage.setItem('crm_pipeline_filters', JSON.stringify({
      search: 'wholesale',
      advanced: { stages: [], owners: [], valueMin: null, valueMax: null, closeDate: null, lastActivity: null },
    }));
    const loaded = loadFilterState();
    expect(loaded.search).toBe('wholesale');
    expect(loaded.advanced.archived).toBeNull();
  });

  it('rejects a corrupt value rather than trusting it', () => {
    sessionStorage.setItem('crm_pipeline_filters', JSON.stringify({
      search: '', advanced: { archived: 'bogus' },
    }));
    expect(loadFilterState().advanced.archived).toBeNull();
  });

  it('round-trips a real selection', () => {
    saveFilterState({ search: '', advanced: advanced({ archived: 'only' }) });
    expect(loadFilterState().advanced.archived).toBe('only');
  });
});
