// @vitest-environment jsdom
//
// jsdom, not the default node env, ONLY because the hidden-stage persistence tests touch
// sessionStorage. Worth stating plainly: this file passed locally under `node` because
// Node 26 exposes `sessionStorage` as a global, while CI's Node does not — so the suite was
// green on a global it never declared, and went red the first time it ran on a different
// runtime. Everything else here is pure and indifferent to the environment.
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { CrmDeal } from '../core/types';
import {
  boardOrder, loadHiddenStages, loadShowClosedStages, openPipelineTotals, saveHiddenStages,
  saveShowClosedStages, stageFromToggleKey, stageLabel, stageToggleKey, visibleStageKeys,
} from './pipelineBoard';

/**
 * A localStorage shim, installed because the RUNNER has none — not because the code wants one.
 *
 * Node's own `localStorage` global is a getter that stays `undefined` without
 * `--localstorage-file`, and it SHADOWS the one jsdom would otherwise install: under this
 * environment `window.localStorage` is the very same undefined. `sessionStorage` is a Node
 * global too but a working one, which is why nothing in this file needed a shim before #124.
 *
 * It is the environment, never the subject. Every assertion below drives the real
 * `loadShowClosedStages`/`saveShowClosedStages` against it, and `refuseWrites` reproduces
 * private mode by throwing exactly where a browser would.
 */
function installLocalStorage(refuseWrites = false): () => void {
  const store = new Map<string, string>();
  const shim = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => {
      if (refuseWrites) throw new Error('QuotaExceededError');
      store.set(k, String(v));
    },
    removeItem: (k: string) => { store.delete(k); },
    clear: () => { store.clear(); },
    key: (i: number) => [...store.keys()][i] ?? null,
    get length() { return store.size; },
  };
  const prev = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', { value: shim, configurable: true, writable: true });
  return () => {
    if (prev) Object.defineProperty(globalThis, 'localStorage', prev);
    else delete (globalThis as { localStorage?: unknown }).localStorage;
  };
}

// Sampled at MODULE LOAD, before any hook has installed the shim — the only moment the
// pristine runner is observable. The shim would be pointless if the runner already had a real
// localStorage, and a future runtime or vitest version could supply one; this file's assertions
// must not silently start measuring THAT instead. Pinned rather than assumed.
const RUNNER_LOCAL_STORAGE = typeof (globalThis as { localStorage?: unknown }).localStorage;

describe('the test environment this file compensates for', () => {
  it('has no localStorage of its own, which is why the shim exists', () => {
    expect(RUNNER_LOCAL_STORAGE).toBe('undefined');
  });
});

let restoreLocalStorage: () => void = () => {};

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

  it('defaults to false — closed stages hidden — with nothing stored', () => {
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
