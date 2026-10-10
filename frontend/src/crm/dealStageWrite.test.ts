// Which endpoint a stage change hits (issue #128).
//
// The distinction under test is ACTION vs CONTENT: `lostReason` being a string — even an
// empty or whitespace one — means the user went through the Mark Lost dialog, and that has
// to reach `POST /mark-lost`, because `PUT /deals/:id` does not zero `probability` and does
// not write the reason to the notes thread. Keying off the reason's emptiness instead would
// give two reps materially different writes for the same gesture.
import { describe, expect, it } from 'vitest';

import type { CrmDeal } from '../core/types';
import { movedDeal, stageWriteRequest } from './dealStageWrite';

const body = (init: RequestInit) => JSON.parse(init.body as string);

describe('stageWriteRequest', () => {
  it('PUTs the stage for an ordinary move (drag, bulk, edit form)', () => {
    const { path, init } = stageWriteRequest(7, 'qualified');
    expect(path).toBe('/api/crm/deals/7');
    expect(init.method).toBe('PUT');
    expect(body(init)).toEqual({ stage: 'qualified' });
  });

  it('POSTs to mark-lost when the Mark Lost dialog supplied a reason', () => {
    const { path, init } = stageWriteRequest(7, 'lost', 'Chose a competitor');
    expect(path).toBe('/api/crm/deals/7/mark-lost');
    expect(init.method).toBe('POST');
    expect(body(init)).toEqual({ lost_reason: 'Chose a competitor' });
  });

  it('still POSTs to mark-lost when the dialog was confirmed with an EMPTY reason', () => {
    // The regression this guards: an `if (lostReason?.trim())` test would send this
    // through the plain PUT, leaving probability untouched on a deal the rep just closed.
    for (const blank of ['', '   ', '\n']) {
      const { path, init } = stageWriteRequest(7, 'lost', blank);
      expect(path).toBe('/api/crm/deals/7/mark-lost');
      expect(body(init)).toEqual({ lost_reason: blank });
    }
  });

  it('PUTs a move to lost that did NOT come from the dialog', () => {
    // Dragging a card into the Lost column, where there is no reason to record.
    const { path, init } = stageWriteRequest(7, 'lost');
    expect(path).toBe('/api/crm/deals/7');
    expect(init.method).toBe('PUT');
    expect(body(init)).toEqual({ stage: 'lost' });
  });

  it('never routes a non-lost stage to mark-lost, even carrying a reason', () => {
    const { path } = stageWriteRequest(7, 'won', 'ignored');
    expect(path).toBe('/api/crm/deals/7');
  });
});

describe('Closed on (#279)', () => {
  it('a Mark Won PUT carries the chosen day; other moves never do', () => {
    expect(body(stageWriteRequest(7, 'won', undefined, '2026-10-01').init))
      .toEqual({ stage: 'won', closed_on: '2026-10-01' });
    expect(body(stageWriteRequest(7, 'won').init)).toEqual({ stage: 'won' });
    expect(body(stageWriteRequest(7, 'qualified', undefined, '2026-10-01').init))
      .toEqual({ stage: 'qualified' });
  });

  it('movedDeal mirrors the server rule: set into won, clear out of won, else untouched', () => {
    const base = { id: 1, title: 't', value: 0, probability: 0, expected_close_date: '' } as CrmDeal;
    const T = '2026-10-10';
    expect(movedDeal({ ...base, stage: 'lead' }, 'won', '2026-10-01', T).closed_on).toBe('2026-10-01');
    expect(movedDeal({ ...base, stage: 'lead' }, 'won', undefined, T).closed_on).toBe(T);
    expect(movedDeal({ ...base, stage: 'won', closed_on: '2026-09-01' }, 'lead', undefined, T).closed_on)
      .toBeNull();
    expect(movedDeal({ ...base, stage: 'won', closed_on: '2026-09-01' }, 'won', '2026-10-01', T).closed_on)
      .toBe('2026-09-01');
    expect(movedDeal({ ...base, stage: 'lead', closed_on: null }, 'qualified', undefined, T))
      .toEqual({ ...base, stage: 'qualified', closed_on: null });
  });
});
