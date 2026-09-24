// @vitest-environment jsdom
import { StrictMode, act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import usePatchableAssembly, {
  applyOverlay, rowIsGone, writeMayHaveLanded, type PatchableAssembly,
} from './usePatchableAssembly';
import type { PageAssembly } from '../shared/collection';
import { ApiError } from '../core/api/client';

interface Row { id: number; name: string; note?: string | null }

const rows = (...r: Row[]) => r;

// ── applyOverlay: the pure fold ─────────────────────────────────────────────

describe('applyOverlay', () => {
  const getId = (r: Row) => r.id;

  it('returns a COPY when the overlay is empty, never the caller array', () => {
    const base = rows({ id: 1, name: 'a' });
    const out = applyOverlay(base, new Map(), getId);
    expect(out).toEqual(base);
    expect(out).not.toBe(base);
  });

  it('MERGES a patch, so a field the response omits is not blanked', () => {
    // The whole reason this is a merge and not a replacement: a write response that
    // lacks a derived column (last_contact_at) or a joined name must not erase it.
    const base = rows({ id: 1, name: 'Ada', note: 'derived' });
    const out = applyOverlay(base, new Map([[1, { name: 'Ada L.' }]]), getId);
    expect(out).toEqual([{ id: 1, name: 'Ada L.', note: 'derived' }]);
  });

  it('lets an EXPLICIT null in the patch overwrite', () => {
    // Unlinking is a real edit — "absent" and "explicitly null" must not be conflated.
    const base = rows({ id: 1, name: 'Ada', note: 'x' });
    const out = applyOverlay(base, new Map([[1, { note: null }]]), getId);
    expect(out[0].note).toBeNull();
  });

  it('places a row unknown to the base FIRST, as a creation', () => {
    const base = rows({ id: 1, name: 'a' }, { id: 2, name: 'b' });
    const out = applyOverlay(base, new Map([[9, { id: 9, name: 'new' }]]), getId);
    expect(out.map(r => r.id)).toEqual([9, 1, 2]);
  });

  it('does not mutate the base rows it patches', () => {
    const original = { id: 1, name: 'Ada' };
    applyOverlay([original], new Map([[1, { name: 'changed' }]]), getId);
    expect(original.name).toBe('Ada');
  });
});

// ── the hook: tombstones and the retry contract ─────────────────────────────

let container: HTMLDivElement;
let root: Root;
const latest: { current: PatchableAssembly<Row> | null } = { current: null };

function Probe({ assembly }: { assembly: PageAssembly<Row> }) {
  const value = usePatchableAssembly(assembly, (r: Row) => r.id);
  useEffect(() => { latest.current = value; });
  return null;
}

function render(assembly: PageAssembly<Row>): void {
  act(() => {
    root.render(<StrictMode><Probe assembly={assembly} /></StrictMode>);
  });
}

const state = (): PatchableAssembly<Row> => {
  if (!latest.current) throw new Error('probe did not render');
  return latest.current;
};

function stub(items: Row[] | null, retry = vi.fn()): PageAssembly<Row> {
  return { items, loading: items === null, error: null, pagesLoaded: 1, itemsLoaded: items?.length ?? 0, retry };
}

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  latest.current = null;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('usePatchableAssembly', () => {
  it('reports null while the sweep is still running', () => {
    render(stub(null));
    expect(state().items).toBeNull();
  });

  it('folds an upsert in without a refetch', () => {
    render(stub(rows({ id: 1, name: 'Ada' })));
    act(() => state().upsert({ id: 1, name: 'Ada L.' }));
    expect(state().items).toEqual([{ id: 1, name: 'Ada L.' }]);
  });

  it('drops a removed row', () => {
    render(stub(rows({ id: 1, name: 'a' }, { id: 2, name: 'b' })));
    act(() => state().remove(1));
    expect(state().items?.map(r => r.id)).toEqual([2]);
  });

  it('restores a removed row if it is upserted again', () => {
    render(stub(rows({ id: 1, name: 'a' })));
    act(() => state().remove(1));
    act(() => state().upsert({ id: 1, name: 'a2' }));
    expect(state().items).toEqual([{ id: 1, name: 'a2' }]);
  });

  it('retry() CLEARS the overlay so a fresh sweep is not overwritten by stale patches', () => {
    // The bug this pins: patch a row, re-sweep, and the pre-retry patch merges back over
    // the server's newer truth. Concretely — completing a repeating todo re-sweeps to pick
    // up the spawned occurrence, and a surviving `completed: 0` would un-complete it.
    const retry = vi.fn();
    const before = stub(rows({ id: 1, name: 'stale-patch-target' }), retry);
    render(before);
    act(() => state().upsert({ id: 1, name: 'LOCAL' }));
    expect(state().items).toEqual([{ id: 1, name: 'LOCAL' }]);

    act(() => state().retry());
    expect(retry).toHaveBeenCalledTimes(1);

    // The re-sweep lands with the server's value; the discarded patch must not reappear.
    render(stub(rows({ id: 1, name: 'SERVER' }), retry));
    expect(state().items).toEqual([{ id: 1, name: 'SERVER' }]);
  });

  it('keeps a write that arrives DURING the new sweep', () => {
    const retry = vi.fn();
    render(stub(rows({ id: 1, name: 'a' }), retry));
    act(() => state().retry());
    // Sweep in flight: the assembly publishes nothing yet.
    render(stub(null, retry));
    act(() => state().upsert({ id: 1, name: 'written-mid-sweep' }));
    expect(state().items).toBeNull();
    // …and it applies once the sweep completes.
    render(stub(rows({ id: 1, name: 'a' }), retry));
    expect(state().items).toEqual([{ id: 1, name: 'written-mid-sweep' }]);
  });
});

// ── write-outcome triage ────────────────────────────────────────────────────

describe('writeMayHaveLanded', () => {
  it('trusts the corpus after a definite 4xx refusal', () => {
    // Nothing was written, so the rows on screen are still correct — re-sweeping here
    // would just be a slow way to render the same thing.
    for (const status of [400, 404, 409, 422, 499]) {
      expect(writeMayHaveLanded(new ApiError('nope', status, ''))).toBe(false);
    }
  });

  it('does NOT trust it after a 5xx, which may have committed before failing', () => {
    for (const status of [500, 502, 503]) {
      expect(writeMayHaveLanded(new ApiError('boom', status, ''))).toBe(true);
    }
  });

  it('does NOT trust it after a transport failure, which carries no status at all', () => {
    // The connection can drop after Postgres commits but before the ack arrives, so
    // "no status" is the most ambiguous outcome of the three, not the safest.
    expect(writeMayHaveLanded(new TypeError('Failed to fetch'))).toBe(true);
    expect(writeMayHaveLanded('something thrown that is not an Error')).toBe(true);
  });
});

describe('rowIsGone', () => {
  it('singles out the one 4xx that proves the CORPUS wrong', () => {
    // 404 means somebody else already deleted the row, so the local copy is a ghost —
    // even though, like every 4xx, the write itself never happened.
    expect(rowIsGone(new ApiError('gone', 404, ''))).toBe(true);
    expect(writeMayHaveLanded(new ApiError('gone', 404, ''))).toBe(false);
  });

  it('is false for every other failure, including ones with no status', () => {
    for (const status of [400, 403, 409, 422, 500, 503]) {
      expect(rowIsGone(new ApiError('x', status, '')), String(status)).toBe(false);
    }
    expect(rowIsGone(new TypeError('Failed to fetch'))).toBe(false);
    expect(rowIsGone('not an error')).toBe(false);
  });
});
