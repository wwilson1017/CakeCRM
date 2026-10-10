import { describe, expect, it } from 'vitest';
import { STAGE_ORDER } from './constants';
import type { StageCriteria } from './stageCriteria';

/**
 * The standard copy's single home since #289 is `backend/crm/stage_criteria_standard.json`: the
 * server merges it with an install's own overrides. Its text is substituted by `define` in
 * `vitest.config.ts` — never imported, because the Docker build type-checks this file from a
 * copy of `frontend/` alone. Declared rather than imported so this file needs no Node types.
 */
declare const __STAGE_CRITERIA_STANDARD__: string;
const STAGE_CRITERIA: Record<string, StageCriteria> = JSON.parse(__STAGE_CRITERIA_STANDARD__);

describe('STAGE_CRITERIA covers exactly the stages the board renders', () => {
  it('every stage has a summary and a non-empty checklist', () => {
    for (const stage of STAGE_ORDER) {
      const entry = STAGE_CRITERIA[stage];
      expect(entry, `no criteria for stage "${stage}"`).toBeDefined();
      expect(entry.summary.length).toBeGreaterThan(0);
      expect(entry.checklist.length).toBeGreaterThan(0);
      expect(entry.checklist.every(item => item.trim().length > 0)).toBe(true);
    }
  });

  it('declares no stage the board does not have', () => {
    expect(Object.keys(STAGE_CRITERIA).sort()).toEqual([...STAGE_ORDER].sort());
  });
});

describe('the copy stays vertical-neutral', () => {
  // This repo goes public, and the blueprint's checklists name one industry's artefacts.
  // The editorial rule is easy to regress by copy-pasting more blueprint text, so it is
  // enforced mechanically rather than left to review.
  const FORBIDDEN = [
    /cheesecake/i, /\bdessert/i, /\bbakery\b/i, /\bchef\b/i, /\bmenu\b/i,
    /\brestaurant/i, /\bdistributor/i, /\bfood\b/i, /\bcatering\b/i, /\brecipe/i,
  ];

  const corpus = Object.values(STAGE_CRITERIA)
    .flatMap(c => [c.summary, ...c.checklist])
    .join(' ');

  for (const pattern of FORBIDDEN) {
    it(`contains no ${pattern.source}`, () => {
      expect(corpus).not.toMatch(pattern);
    });
  }
});
