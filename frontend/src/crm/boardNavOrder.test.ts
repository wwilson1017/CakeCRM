// The ‹ › order must BE the board — same columns, same order, same within-column sort.
//
// The failure this guards against is quiet: an order built from `STAGE_ORDER` instead of the
// caller's visible columns would page the user through cards the stage facet is hiding, and an
// order that flattened row-major would jump between columns. Both look like "navigation works"
// until you use it.
import { describe, expect, it } from 'vitest';
import type { CrmDeal } from '../core/types';
import { boardNavOrder } from './boardNavOrder';

function deal(id: number): CrmDeal {
  return {
    id, title: `Deal ${id}`, stage: 'lead', value: 0, notes: '',
    contact_id: null, company_id: null, expected_close_date: '', probability: 0,
    currency: 'USD', created_at: '', updated_at: '',
  };
}

const grouped = {
  lead: [deal(1), deal(2)],
  qualified: [deal(3)],
  proposal: [],
  negotiation: [deal(4)],
  won: [deal(5)],
  lost: [deal(6)],
};

describe('boardNavOrder', () => {
  it('flattens column-major in the caller order, preserving each column order', () => {
    expect(boardNavOrder(['lead', 'qualified', 'negotiation'], grouped)).toEqual([1, 2, 3, 4]);
  });

  it('walks only the columns actually rendered', () => {
    // The stage facet hides columns; a hidden column is not something the user is looking at, so
    // ‹ › must not page into it.
    expect(boardNavOrder(['qualified'], grouped)).toEqual([3]);
    expect(boardNavOrder(['won', 'lost'], grouped)).toEqual([5, 6]);
  });

  it('honours the caller order even when it differs from the canonical stage order', () => {
    expect(boardNavOrder(['won', 'lead'], grouped)).toEqual([5, 1, 2]);
  });

  it('skips empty and absent columns without gaps', () => {
    expect(boardNavOrder(['lead', 'proposal', 'qualified'], grouped)).toEqual([1, 2, 3]);
    expect(boardNavOrder(['lead', 'nonexistent'], grouped)).toEqual([1, 2]);
  });

  it('returns an empty order when nothing is on the board', () => {
    // `[]` is a real answer to `CollectionDetail` — "nothing to navigate", both arrows disabled —
    // and is what the filtered-to-zero board must produce.
    expect(boardNavOrder([], grouped)).toEqual([]);
    expect(boardNavOrder(['lead', 'qualified'], {})).toEqual([]);
  });
});
