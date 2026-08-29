// Which endpoint a stage change hits (issue #128).
//
// The distinction under test is ACTION vs CONTENT: `lostReason` being a string — even an
// empty or whitespace one — means the user went through the Mark Lost dialog, and that has
// to reach `POST /mark-lost`, because `PUT /deals/:id` does not zero `probability` and does
// not write the reason to the notes thread. Keying off the reason's emptiness instead would
// give two reps materially different writes for the same gesture.
import { describe, expect, it } from 'vitest';

import { stageWriteRequest } from './dealStageWrite';

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
