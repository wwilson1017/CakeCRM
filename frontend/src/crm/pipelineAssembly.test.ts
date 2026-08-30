import { describe, it, expect, vi, beforeEach } from 'vitest';
import { CRM_LIST_PAGE_SIZE } from './assemblyPage';
import { MAX_PAGES } from '../shared/collection/usePageAssembly';

const apiMock = vi.fn();
vi.mock('../core/api/client', () => ({
  api: (path: string, init?: RequestInit) => apiMock(path, init),
}));

const { sweepPipelineDeals, SweepSupersededError } = await import('./pipelineAssembly');

/** A deal shaped just enough for the sweep: an id and the recency key it sorts on. */
const deal = (id: number, updated_at = '2026-08-01T00:00:00+00:00') => ({ id, updated_at });

/** `n` deals with ids counting up from `from`, all sharing one timestamp so the assembled
 *  order is decided by the id tiebreaker alone. */
const page = (from: number, n: number) =>
  Array.from({ length: n }, (_, i) => deal(from + i));

beforeEach(() => apiMock.mockReset());

describe('sweepPipelineDeals', () => {
  it('walks pages until the server stops over-filling, keeping every deal exactly once', async () => {
    apiMock
      .mockResolvedValueOnce({ deals: page(1, CRM_LIST_PAGE_SIZE + 1) }) // 501 → more
      .mockResolvedValueOnce({ deals: page(CRM_LIST_PAGE_SIZE + 1, 120) }); // 120 → done

    const out = await sweepPipelineDeals(false);
    const ids = out.map(d => d.id);

    expect(apiMock).toHaveBeenCalledTimes(2);
    expect(ids).toHaveLength(620);
    expect(new Set(ids).size).toBe(620);
    // The surplus 501st row of page 0 is DROPPED and re-fetched as the head of page 1 —
    // it must appear exactly once, never twice and never not at all.
    expect(ids.filter(id => id === CRM_LIST_PAGE_SIZE + 1)).toEqual([CRM_LIST_PAGE_SIZE + 1]);
  });

  it('cursors from the last KEPT row, not the surplus probe row', async () => {
    apiMock
      .mockResolvedValueOnce({ deals: page(1, CRM_LIST_PAGE_SIZE + 1) })
      .mockResolvedValueOnce({ deals: page(CRM_LIST_PAGE_SIZE + 1, 1) });

    await sweepPipelineDeals(false);

    expect(apiMock.mock.calls[0][0]).toBe('/api/crm/deals?sort=id&limit=501');
    expect(apiMock.mock.calls[1][0]).toBe(
      `/api/crm/deals?sort=id&limit=501&after_id=${CRM_LIST_PAGE_SIZE}`,
    );
  });

  it('makes exactly one request for a corpus that fits in a page', async () => {
    apiMock.mockResolvedValueOnce({ deals: page(1, 3) });

    const out = await sweepPipelineDeals(false);

    expect(apiMock).toHaveBeenCalledTimes(1);
    expect(apiMock.mock.calls[0][0]).not.toContain('after_id');
    expect(out).toHaveLength(3);
  });

  it('carries include_archived on EVERY page, so two corpora can never interleave', async () => {
    apiMock
      .mockResolvedValueOnce({ deals: page(1, CRM_LIST_PAGE_SIZE + 1) })
      .mockResolvedValueOnce({ deals: page(CRM_LIST_PAGE_SIZE + 1, 1) });

    await sweepPipelineDeals(true);

    expect(apiMock.mock.calls[0][0]).toContain('include_archived=true');
    expect(apiMock.mock.calls[1][0]).toContain('include_archived=true');
  });

  it('restores the server recency order the board sorts underneath', async () => {
    // Pages arrive id ASC (cursor order). PipelinePage sorts columns by lead_score with a
    // STABLE sort and relies on recency surviving beneath equal scores — the common case,
    // since lead_score is NULL until something recomputes it. So the sweep must hand back
    // newest-first, not id-ascending.
    apiMock.mockResolvedValueOnce({
      deals: [
        deal(1, '2026-08-01T00:00:00+00:00'),
        deal(2, '2026-08-03T00:00:00+00:00'),
        deal(3, '2026-08-02T00:00:00+00:00'),
      ],
    });

    expect((await sweepPipelineDeals(false)).map(d => d.id)).toEqual([2, 3, 1]);
  });

  it('breaks ties on id DESC, matching the servers total order', async () => {
    apiMock.mockResolvedValueOnce({ deals: [deal(1), deal(2), deal(3)] });

    expect((await sweepPipelineDeals(false)).map(d => d.id)).toEqual([3, 2, 1]);
  });

  it('stops a superseded sweep before its next page, leaving one request in flight', async () => {
    apiMock.mockResolvedValue({ deals: page(1, CRM_LIST_PAGE_SIZE + 1) });
    let live = true;

    const promise = sweepPipelineDeals(false, () => live);
    live = false;

    await expect(promise).rejects.toBeInstanceOf(SweepSupersededError);
    // Parity with the single GET this replaced: at most ONE request outlives the load.
    expect(apiMock).toHaveBeenCalledTimes(1);
  });

  it('refuses to spin forever when the server always claims another page', async () => {
    apiMock.mockResolvedValue({ deals: page(1, CRM_LIST_PAGE_SIZE + 1) });

    await expect(sweepPipelineDeals(false)).rejects.toThrow(/did not terminate/);
    expect(apiMock).toHaveBeenCalledTimes(MAX_PAGES);
  });

  it('hands every page an abort signal, so flaky Wi-Fi cannot pin the board forever', async () => {
    // The per-page timeout is what makes a HANG fail instead of stalling the board
    // forever. Its clock is usePageAssembly's own constant; what this pins is the wiring
    // the constant is useless without — that a real signal reaches the transport, live,
    // on every page.
    const signals: (AbortSignal | null | undefined)[] = [];
    apiMock.mockImplementation((_path: string, init?: RequestInit) => {
      signals.push(init?.signal);
      return Promise.resolve({
        deals: signals.length === 1 ? page(1, CRM_LIST_PAGE_SIZE + 1) : page(1, 1),
      });
    });

    await sweepPipelineDeals(false);

    expect(signals).toHaveLength(2);
    for (const signal of signals) {
      expect(signal).toBeInstanceOf(AbortSignal);
      expect(signal?.aborted).toBe(false);
    }
  });

  it('propagates a failed page instead of resolving a partial board', async () => {
    // A partial corpus is the one thing this must never hand back: every facet, total and
    // bulk intersection downstream would silently describe a subset.
    apiMock
      .mockResolvedValueOnce({ deals: page(1, CRM_LIST_PAGE_SIZE + 1) })
      .mockRejectedValueOnce(new Error('Failed to fetch'));

    await expect(sweepPipelineDeals(false)).rejects.toThrow('Failed to fetch');
  });
});
