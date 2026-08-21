import { describe, expect, it } from 'vitest';

import { classifyBulkMove, describeBulkMove } from './bulkOutcome';

const ok = (updated: number, errors: string[] = []) =>
  ({ ok: true, updated, updated_ids: [], errors });

describe('classifyBulkMove', () => {
  it('reads a fully successful response as clean', () => {
    expect(classifyBulkMove(ok(3))).toEqual({ kind: 'clean' });
  });

  it('reads per-deal errors alongside ok:true as skips, keeping the server reasons', () => {
    expect(classifyBulkMove(ok(2, ['Deal 7 not found'])))
      .toEqual({ kind: 'skips', skipped: 1, reasons: ['Deal 7 not found'] });
  });

  it('reads ok:false as a rejection carrying the server reason', () => {
    expect(classifyBulkMove({ ok: false, updated: 0, errors: ['Invalid stage: nope'] }))
      .toEqual({ kind: 'rejected', reason: 'Invalid stage: nope' });
  });

  it('reads ok:false with no errors as a rejection with no reason', () => {
    expect(classifyBulkMove({ ok: false, updated: 0 })).toEqual({ kind: 'rejected', reason: undefined });
  });

  it('treats a thrown 4xx as a refusal, never as doubt', () => {
    // FastAPI validates before the handler runs, so the request provably never reached a
    // write. Calling this "unconfirmed" would manufacture doubt about unchanged deals.
    expect(classifyBulkMove({ thrown: { status: 422, reason: 'bad body' } }))
      .toEqual({ kind: 'rejected', reason: 'bad body' });
  });

  it('treats a thrown 5xx as unconfirmed', () => {
    expect(classifyBulkMove({ thrown: { status: 502 } })).toEqual({ kind: 'unconfirmed' });
  });

  it('treats a transport failure with no status as unconfirmed', () => {
    // The connection can drop after Postgres commits but before the ack arrives.
    expect(classifyBulkMove({ thrown: {} })).toEqual({ kind: 'unconfirmed' });
  });
});

describe('describeBulkMove', () => {
  it('says nothing at all about a clean run', () => {
    expect(describeBulkMove({ kind: 'clean' }, 4)).toBeNull();
  });

  it('uses the server reason verbatim for a single skip', () => {
    // The two skip causes are not interchangeable: an archived deal needs restoring, a
    // missing one needs nothing. A generic "no longer found" would lie about the first.
    const notice = describeBulkMove(
      { kind: 'skips', skipped: 1, reasons: ['Cannot change the stage of archived deal #9 — restore it first'] },
      5, true,
    );
    expect(notice).toEqual({
      text: 'Cannot change the stage of archived deal #9 — restore it first.',
      persistent: false,
    });
  });

  it('summarises several skips but still names the first cause', () => {
    const notice = describeBulkMove(
      { kind: 'skips', skipped: 3, reasons: ['Deal 7 not found', 'Deal 8 not found'] }, 9, true,
    );
    expect(notice?.text).toBe("3 deals couldn't be moved — first: Deal 7 not found.");
    expect(notice?.persistent).toBe(false);
  });

  it('falls back to a bare count when the server sent no reasons', () => {
    expect(describeBulkMove({ kind: 'skips', skipped: 2, reasons: [] }, 4)?.text)
      .toBe("2 deals couldn't be moved.");
  });

  it('makes a skip notice persistent and explicit when the board could not refresh', () => {
    const notice = describeBulkMove(
      { kind: 'skips', skipped: 1, reasons: ['Deal 7 not found'] }, 5, false,
    );
    expect(notice?.persistent).toBe(true);
    expect(notice?.text).toContain('Deal 7 not found');
    expect(notice?.text).toContain('could not be refreshed');
  });

  it('names the reason on a rejection', () => {
    const notice = describeBulkMove({ kind: 'rejected', reason: 'Invalid stage: nope' }, 3);
    expect(notice).toEqual({ text: '3 deals not moved — Invalid stage: nope.', persistent: false });
  });

  it('trims a server reason that supplies its own punctuation', () => {
    const notice = describeBulkMove({ kind: 'rejected', reason: 'Too many deals (201).' }, 201);
    expect(notice?.text).toBe('201 deals not moved — Too many deals (201).');
  });

  it('renders a rejection with no usable reason as a plain sentence', () => {
    expect(describeBulkMove({ kind: 'rejected' }, 1)?.text).toBe('1 deal not moved.');
  });

  it('never marks a rejection persistent — the caller has already reverted the board', () => {
    expect(describeBulkMove({ kind: 'rejected', reason: 'x' }, 2, false)?.persistent).toBe(false);
  });

  it('says the outcome is unknown, and stays put, when the request could not be confirmed', () => {
    const notice = describeBulkMove({ kind: 'unconfirmed' }, 6, true);
    expect(notice?.persistent).toBe(true);
    expect(notice?.text).toContain("Couldn't confirm whether 6 deals moved");
    expect(notice?.text).toContain('refreshed to what actually saved');
    // Must never claim nothing was saved: the commit may have landed before the drop.
    expect(notice?.text).not.toContain('not moved');
  });

  it('tells the operator to reload when it could not confirm AND could not refresh', () => {
    const notice = describeBulkMove({ kind: 'unconfirmed' }, 1, false);
    expect(notice?.persistent).toBe(true);
    expect(notice?.text).toContain('Reload the page');
  });

  it('pluralises a single deal correctly across every branch', () => {
    expect(describeBulkMove({ kind: 'rejected' }, 1)?.text).toContain('1 deal not');
    expect(describeBulkMove({ kind: 'unconfirmed' }, 1)?.text).toContain('1 deal moved');
    expect(describeBulkMove({ kind: 'skips', skipped: 1, reasons: [] }, 1)?.text)
      .toContain('1 deal ');
  });
});
