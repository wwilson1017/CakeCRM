import { describe, expect, it } from 'vitest';

import { applicableBulkIds } from './bulkSelection';

const visible = (...ids: number[]) => ids.map(id => ({ id }));

describe('applicableBulkIds', () => {
  it('keeps only the selected ids that are still visible', () => {
    expect(applicableBulkIds(new Set([1, 2, 3]), visible(1, 3))).toEqual([1, 3]);
  });

  it('drops a selection a filter has hidden entirely', () => {
    // The Set keeps them (clearing the filter brings them back); the APPLY set must not,
    // or a click made about the visible deals would move hidden ones too.
    expect(applicableBulkIds(new Set([7, 8]), visible(1, 2))).toEqual([]);
  });

  it('preserves selection order, not the visible order', () => {
    // Load-bearing: this is the order ids reach the server, so it is the order any
    // per-deal errors come back in.
    expect(applicableBulkIds(new Set([3, 1, 2]), visible(1, 2, 3))).toEqual([3, 1, 2]);
  });

  it('drops stale ids for deals that have left the board', () => {
    expect(applicableBulkIds(new Set([5, 6, 9]), visible(5, 6))).toEqual([5, 6]);
  });

  it('handles the empty cases', () => {
    expect(applicableBulkIds(new Set(), visible(1, 2))).toEqual([]);
    expect(applicableBulkIds(new Set([1]), [])).toEqual([]);
  });
});
