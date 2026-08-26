import { beforeEach, describe, expect, it } from 'vitest';
import type { CrmDeal } from '../core/types';
import {
  boardOrder, loadHiddenStages, openPipelineTotals, saveHiddenStages,
  stageFromToggleKey, stageLabel, stageToggleKey, visibleStageKeys,
} from './pipelineBoard';

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

  it('returns an empty set when nothing is stored', () => {
    expect(loadHiddenStages().size).toBe(0);
  });

  it('tolerates malformed JSON rather than blanking the board', () => {
    sessionStorage.setItem('crm_pipeline_hidden_stages', '{not json');
    expect(loadHiddenStages().size).toBe(0);
  });

  it('tolerates a non-array payload', () => {
    sessionStorage.setItem('crm_pipeline_hidden_stages', JSON.stringify({ won: true }));
    expect(loadHiddenStages().size).toBe(0);
  });

  it('drops unknown stage names so a stale key cannot accumulate', () => {
    sessionStorage.setItem('crm_pipeline_hidden_stages', JSON.stringify(['won', 'nonesuch', 42]));
    expect([...loadHiddenStages()]).toEqual(['won']);
  });
});
