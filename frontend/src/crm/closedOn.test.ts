// The Closed on rules the edit form applies before it saves (#279).
import { describe, expect, it } from 'vitest';

import { closedOnEditError, closedOnIsValid } from './closedOn';

const TODAY = '2026-10-10';

describe('closedOnIsValid', () => {
  it('takes today and the past, refuses the future and anything not a whole day', () => {
    expect(closedOnIsValid('2026-10-10', TODAY)).toBe(true);
    expect(closedOnIsValid('2020-01-01', TODAY)).toBe(true);
    expect(closedOnIsValid('2026-10-11', TODAY)).toBe(false);
    expect(closedOnIsValid('', TODAY)).toBe(false);
    expect(closedOnIsValid('2026-1-1', TODAY)).toBe(false);
  });
});

describe('closedOnEditError', () => {
  it('only judges a Won form', () => {
    expect(closedOnEditError({ stage: 'lead', closed_on: '2099-01-01' }, { closed_on: '' }, TODAY))
      .toBe('');
  });

  it('refuses blanking a date the deal has, but lets an undated Won deal stay undated', () => {
    expect(closedOnEditError({ stage: 'won', closed_on: '' }, { closed_on: '2026-10-01' }, TODAY))
      .toMatch(/cannot be blank/);
    expect(closedOnEditError({ stage: 'won', closed_on: '' }, { closed_on: '' }, TODAY)).toBe('');
  });

  it('refuses a future day and accepts a past one', () => {
    expect(closedOnEditError({ stage: 'won', closed_on: '2026-10-11' }, { closed_on: '' }, TODAY))
      .toMatch(/future/);
    expect(closedOnEditError({ stage: 'won', closed_on: '2026-10-01' }, { closed_on: '' }, TODAY))
      .toBe('');
  });
});
