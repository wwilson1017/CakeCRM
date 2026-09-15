// @vitest-environment jsdom
//
// jsdom, not the default node env, ONLY because the hidden-stage persistence tests touch
// sessionStorage. Worth stating plainly: this file passed locally under `node` because
// Node 26 exposes `sessionStorage` as a global, while CI's Node does not — so the suite was
// green on a global it never declared, and went red the first time it ran on a different
// runtime. Everything else here is pure and indifferent to the environment.
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { CrmDeal } from '../core/types';
import { installLocalStorage } from './testStorage';
import {
  boardColumnLayout, boardOrder, lastContactLabel, loadHiddenStages, loadShowClosedStages,
  openPipelineTotals, saveHiddenStages, saveShowClosedStages, stageFromToggleKey, stageLabel,
  stageToggleKey, visibleStageKeys,
} from './pipelineBoard';

let restoreLocalStorage: () => void = () => {};

// A fresh, empty store for EVERY test. This cannot be left to the host: the two runners this
// suite meets disagree about whether `localStorage` exists at all, and that decides both what
// these assertions measure and whether one case seeds the next. See `testStorage.ts`.
beforeEach(() => { restoreLocalStorage = installLocalStorage(); });
afterEach(() => { restoreLocalStorage(); });

function deal(over: Partial<CrmDeal> & { id: number }): CrmDeal {
  return {
    contact_id: null, company_id: null, title: `Deal ${over.id}`, stage: 'lead',
    value: 0, notes: '', expected_close_date: '', probability: 0, currency: 'USD',
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    ...over,
  };
}

describe('boardOrder', () => {
  it('is stage-major in STAGE_ORDER, not input order', () => {
    const out = boardOrder([
      deal({ id: 1, stage: 'won' }),
      deal({ id: 2, stage: 'lead' }),
      deal({ id: 3, stage: 'proposal' }),
    ]);
    expect(out.map(d => d.stage)).toEqual(['lead', 'proposal', 'won']);
  });

  it('orders by lead_score DESC within a stage, with unscored deals last', () => {
    const out = boardOrder([
      deal({ id: 1, lead_score: 20 }),
      deal({ id: 2, lead_score: null }),
      deal({ id: 3, lead_score: 90 }),
      deal({ id: 4 }), // lead_score absent entirely
    ]);
    expect(out.map(d => d.id)).toEqual([3, 1, 2, 4]);
  });

  it('is stable for equal scores — the server updated_at DESC order survives', () => {
    const out = boardOrder([
      deal({ id: 1, lead_score: 50 }),
      deal({ id: 2, lead_score: 50 }),
      deal({ id: 3, lead_score: 50 }),
    ]);
    expect(out.map(d => d.id)).toEqual([1, 2, 3]);
  });

  it('sorts an unknown stage last rather than dropping it', () => {
    const out = boardOrder([deal({ id: 1, stage: 'archived-elsewhere' }), deal({ id: 2, stage: 'lost' })]);
    expect(out.map(d => d.id)).toEqual([2, 1]);
  });

  it('returns a NEW array of the SAME element identities and leaves the input untouched', () => {
    const a = deal({ id: 1, stage: 'won' });
    const b = deal({ id: 2, stage: 'lead' });
    const input = [a, b];
    const out = boardOrder(input);
    expect(out).not.toBe(input);
    expect(input.map(d => d.id)).toEqual([1, 2]); // not mutated in place
    // Identity, not a clone — the layer's memos and shared/dnd's wrapper key on it.
    expect(out[0]).toBe(b);
    expect(out[1]).toBe(a);
  });

  it('handles an empty array', () => {
    expect(boardOrder([])).toEqual([]);
  });
});

describe('stage toggle keys', () => {
  it('round-trips a stage', () => {
    expect(stageFromToggleKey(stageToggleKey('qualified'))).toBe('qualified');
  });

  it("returns '' for a key that is not this board's", () => {
    expect(stageFromToggleKey('showClosed')).toBe('');
    expect(stageFromToggleKey('')).toBe('');
  });
});

describe('stageLabel', () => {
  it('title-cases the lowercase stage constants', () => {
    expect(stageLabel('lead')).toBe('Lead');
    expect(stageLabel('negotiation')).toBe('Negotiation');
  });
});

describe('visibleStageKeys', () => {
  it('shows every stage when nothing is hidden and no facet is selected', () => {
    expect(visibleStageKeys(new Set(), [])).toEqual(['lead', 'qualified', 'proposal', 'negotiation', 'won', 'lost']);
  });

  it('drops hidden stages, preserving STAGE_ORDER', () => {
    expect(visibleStageKeys(new Set(['won', 'lost']), [])).toEqual(['lead', 'qualified', 'proposal', 'negotiation']);
  });

  it('narrows to the stage facet when one is selected', () => {
    expect(visibleStageKeys(new Set(), ['proposal', 'lead'])).toEqual(['lead', 'proposal']);
  });

  it('hiding beats the facet — a put-away column stays away even when the facet selects it', () => {
    expect(visibleStageKeys(new Set(['proposal']), ['proposal', 'lead'])).toEqual(['lead']);
  });

  it('can legitimately render no columns at all', () => {
    expect(visibleStageKeys(new Set(['lead']), ['lead'])).toEqual([]);
  });
});

describe('openPipelineTotals', () => {
  it('sums open stages only — won and lost are excluded from "open"', () => {
    const totals = openPipelineTotals([
      deal({ id: 1, stage: 'lead', value: 100 }),
      deal({ id: 2, stage: 'negotiation', value: 400 }),
      deal({ id: 3, stage: 'won', value: 9999 }),
      deal({ id: 4, stage: 'lost', value: 9999 }),
    ]);
    expect(totals).toEqual({ openTotal: 500, openCount: 2 });
  });

  it('treats a missing or zero value as zero but still counts the deal', () => {
    const totals = openPipelineTotals([
      deal({ id: 1, stage: 'lead', value: 0 }),
      deal({ id: 2, stage: 'lead', value: undefined as unknown as number }),
    ]);
    expect(totals).toEqual({ openTotal: 0, openCount: 2 });
  });

  it('is zero for an empty board', () => {
    expect(openPipelineTotals([])).toEqual({ openTotal: 0, openCount: 0 });
  });
});

describe('hidden-stage persistence', () => {
  beforeEach(() => sessionStorage.clear());

  it('round-trips through sessionStorage', () => {
    saveHiddenStages(new Set(['won', 'lost']));
    expect([...loadHiddenStages()].sort()).toEqual(['lost', 'won']);
  });

  it('falls back to the closed stages when nothing is stored (#124)', () => {
    // The default IS the feature: a fresh tab starts with won/lost put away.
    expect([...loadHiddenStages()].sort()).toEqual(['lost', 'won']);
  });

  it('tolerates malformed JSON rather than blanking the board', () => {
    sessionStorage.setItem('crm_pipeline_hidden_stages', '{not json');
    expect([...loadHiddenStages()].sort()).toEqual(['lost', 'won']);
  });

  it('tolerates a non-array payload', () => {
    sessionStorage.setItem('crm_pipeline_hidden_stages', JSON.stringify({ won: true }));
    expect([...loadHiddenStages()].sort()).toEqual(['lost', 'won']);
  });

  it('honours a stored EMPTY array rather than re-seeding the default', () => {
    // '[]' is what the header's "Show all" writes. Treating it as "nothing stored" would
    // undo that button on the very next mount — the board would put won/lost straight back.
    sessionStorage.setItem('crm_pipeline_hidden_stages', '[]');
    expect(loadHiddenStages().size).toBe(0);
  });

  it('drops unknown stage names so a stale key cannot accumulate', () => {
    sessionStorage.setItem('crm_pipeline_hidden_stages', JSON.stringify(['won', 'nonesuch', 42]));
    expect([...loadHiddenStages()]).toEqual(['won']);
  });
});

describe('the durable show-closed preference (#124)', () => {
  beforeEach(() => sessionStorage.clear());

  it('is measuring a REAL store, so none of the cases below is vacuous', () => {
    // `loadShowClosedStages` swallows its own error and answers `false`, so with no working
    // localStorage most of this block would pass while proving nothing — which is what a dev
    // machine without one actually did. Assert the store round-trips before trusting the rest.
    saveShowClosedStages(true);
    expect(localStorage.getItem('cakecrm_pipeline_show_closed')).toBe('true');
    expect(loadShowClosedStages()).toBe(true);
  });

  it('defaults to false — closed stages hidden — with nothing stored', () => {
    // Doubles as the leak check: the case above turned the preference ON, and this reads
    // false only because every test gets its own store.
    expect(loadShowClosedStages()).toBe(false);
  });

  it('round-trips through localStorage', () => {
    saveShowClosedStages(true);
    expect(loadShowClosedStages()).toBe(true);
    saveShowClosedStages(false);
    expect(loadShowClosedStages()).toBe(false);
  });

  it('reads only the exact string, so junk means the default', () => {
    localStorage.setItem('cakecrm_pipeline_show_closed', 'yes');
    expect(loadShowClosedStages()).toBe(false);
  });

  it('seeds a fresh tab from the preference', () => {
    saveShowClosedStages(true);
    sessionStorage.clear(); // a NEW tab: durable preference, no session state
    expect(loadHiddenStages().size).toBe(0);
  });

  it('is outranked by a stored per-tab set, in both directions', () => {
    // The whole reason there are two keys: a tab-local reveal (or hide) is the stronger,
    // more recent statement, and must not be overwritten by the standing default.
    saveShowClosedStages(false);
    sessionStorage.setItem('crm_pipeline_hidden_stages', '[]');
    expect(loadHiddenStages().size).toBe(0);

    saveShowClosedStages(true);
    sessionStorage.setItem('crm_pipeline_hidden_stages', JSON.stringify(['won']));
    expect([...loadHiddenStages()]).toEqual(['won']);
  });

  it('reconciles THIS tab, or the Settings toggle would look inert', () => {
    // PipelinePage persists its hidden set on first render, so anyone arriving at Settings
    // from the board already has one — and a stored set outranks the preference. Without
    // this reconciliation, flipping the toggle would change nothing on the way back.
    sessionStorage.setItem('crm_pipeline_hidden_stages', JSON.stringify(['won', 'lost']));

    saveShowClosedStages(true);
    expect(loadHiddenStages().size).toBe(0);

    saveShowClosedStages(false);
    expect([...loadHiddenStages()].sort()).toEqual(['lost', 'won']);
  });

  it('reconciles ONLY the closed stages, leaving a manual hide alone', () => {
    sessionStorage.setItem('crm_pipeline_hidden_stages', JSON.stringify(['won', 'lost', 'proposal']));
    saveShowClosedStages(true);
    expect([...loadHiddenStages()]).toEqual(['proposal']);

    saveShowClosedStages(false);
    expect([...loadHiddenStages()].sort()).toEqual(['lost', 'proposal', 'won']);
  });

  it('still follows the click for this tab when localStorage is refused', () => {
    // Private mode / quota. The preference cannot be REMEMBERED, but the toggle must not
    // become a dead control for the session the person is actually in — the same degradation
    // `useTheme` documents ("theme still applies for this session").
    restoreLocalStorage();
    restoreLocalStorage = installLocalStorage(true);

    sessionStorage.setItem('crm_pipeline_hidden_stages', JSON.stringify(['won', 'lost']));
    saveShowClosedStages(true);
    expect(loadHiddenStages().size).toBe(0);
    expect(loadShowClosedStages()).toBe(false); // not remembered, as expected
  });
});

describe('lastContactLabel', () => {
  // A fixed clock: the label is a duration, so reading the wall clock would make these tests
  // depend on when they run.
  const now = new Date('2026-09-09T12:00:00Z');

  it('names the gap since the last logged note or activity', () => {
    expect(lastContactLabel('2026-09-04T12:00:00Z', now)).toBe('Last contact 5d ago');
    expect(lastContactLabel('2026-08-19T12:00:00Z', now)).toBe('Last contact 3w ago');
    expect(lastContactLabel('2026-06-09T12:00:00Z', now)).toBe('Last contact 3mo ago');
  });

  it('drops the "ago" inside the first day, where it reads wrong', () => {
    expect(lastContactLabel('2026-09-09T09:00:00Z', now)).toBe('Last contact today');
  });

  it('says so outright when nothing has been logged', () => {
    // Never an empty slot: a won account nobody has followed up on is the single strongest
    // signal this line exists to surface, so hiding it would invert the feature (#128's rule
    // that an absent value is a state worth reading).
    expect(lastContactLabel(null, now)).toBe('No contact logged');
    expect(lastContactLabel(undefined, now)).toBe('No contact logged');
    expect(lastContactLabel('', now)).toBe('No contact logged');
  });
});

describe('boardColumnLayout', () => {
  // STAGE_ORDER has six stages, so "nothing hidden" is the 6 case and #124's default (won and
  // lost put away) is the 4 case — the two boards a rep actually starts from.
  it('leaves the full board compact, so the default view is not a redesign', () => {
    expect(boardColumnLayout(6).density).toBe('compact');
    expect(boardColumnLayout(5).density).toBe('compact');
  });

  it('goes roomy at 3-4 visible stages', () => {
    expect(boardColumnLayout(4).density).toBe('roomy');
    expect(boardColumnLayout(3).density).toBe('roomy');
  });

  it('goes wide at 1-2 visible stages', () => {
    expect(boardColumnLayout(2).density).toBe('wide');
    expect(boardColumnLayout(1).density).toBe('wide');
  });

  // Every stage hidden, or a facet matching none. The board renders no columns and shows
  // EmptyFilterState, so the tier is unused — it just must not throw or return junk.
  it('answers for an empty board rather than throwing', () => {
    expect(boardColumnLayout(0).density).toBe('wide');
  });

  it('never narrows a column below the fixed width it has today', () => {
    for (const n of [0, 1, 2, 3, 4, 5, 6, 20]) {
      expect(boardColumnLayout(n).minWidth).toBe(288);
    }
  });

  // The ceiling is what turns freed width into wider columns, and it has to move WITH the
  // density tier: a tier that adds fields but not room would just make the card taller.
  it('raises the ceiling as the board narrows, and always leaves room to grow', () => {
    const [wide, roomy, compact] = [boardColumnLayout(2), boardColumnLayout(4), boardColumnLayout(6)];
    expect(wide.maxWidth).toBeGreaterThan(roomy.maxWidth);
    expect(roomy.maxWidth).toBeGreaterThan(compact.maxWidth);
    expect(compact.maxWidth).toBeGreaterThan(compact.minWidth);
  });
});
