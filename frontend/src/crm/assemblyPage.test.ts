import { describe, it, expect } from 'vitest';
import {
  CRM_LIST_PAGE_SIZE,
  assemblyPageParams,
  splitAssemblyPage,
  nextCursor,
} from './assemblyPage';

const row = (id: number) => ({ id });

describe('assemblyPageParams', () => {
  it('always pins the immutable id order and over-asks by exactly one row', () => {
    const p = assemblyPageParams(null);
    expect(p.get('sort')).toBe('id');
    // The +1 is the hasMore probe — asking for exactly a page would make "full page"
    // ambiguous between "more to come" and "that was all".
    expect(p.get('limit')).toBe(String(CRM_LIST_PAGE_SIZE + 1));
  });

  it('omits the cursor on the first page and sends it afterwards', () => {
    expect(assemblyPageParams(null).has('after_id')).toBe(false);
    expect(assemblyPageParams(4210).get('after_id')).toBe('4210');
  });

  it('stays inside the endpoints le=1000 cap', () => {
    expect(CRM_LIST_PAGE_SIZE + 1).toBeLessThanOrEqual(1000);
  });

  it('sends a cursor of 0 rather than dropping it', () => {
    // A falsy-but-real id must not be confused with "no cursor". Postgres SERIALs start
    // at 1 so id 0 should not occur, but a `if (afterId)` guard would be a latent trap.
    expect(assemblyPageParams(0).get('after_id')).toBe('0');
  });
});

describe('splitAssemblyPage', () => {
  it('reports more and drops the probe row when the page overflows', () => {
    const rows = Array.from({ length: CRM_LIST_PAGE_SIZE + 1 }, (_, i) => row(i + 1));
    const { items, hasMore } = splitAssemblyPage(rows);
    expect(hasMore).toBe(true);
    expect(items).toHaveLength(CRM_LIST_PAGE_SIZE);
    // The probe row is NOT kept; it returns as the head of the next page.
    expect(items[items.length - 1].id).toBe(CRM_LIST_PAGE_SIZE);
  });

  it('reports done on an exactly-full page, which OFFSET+length could not', () => {
    const rows = Array.from({ length: CRM_LIST_PAGE_SIZE }, (_, i) => row(i + 1));
    const { items, hasMore } = splitAssemblyPage(rows);
    expect(hasMore).toBe(false);
    expect(items).toHaveLength(CRM_LIST_PAGE_SIZE);
  });

  it('reports done on a short page and on an empty one', () => {
    expect(splitAssemblyPage([row(1), row(2)])).toEqual({ items: [row(1), row(2)], hasMore: false });
    expect(splitAssemblyPage([])).toEqual({ items: [], hasMore: false });
  });

  it('copies rather than aliasing the caller array', () => {
    const rows = [row(1)];
    const { items } = splitAssemblyPage(rows);
    expect(items).not.toBe(rows);
  });
});

describe('nextCursor', () => {
  it('takes the last KEPT row so the probe row is re-fetched, never skipped', () => {
    const rows = Array.from({ length: CRM_LIST_PAGE_SIZE + 1 }, (_, i) => row(i + 1));
    const { items } = splitAssemblyPage(rows);
    // Cursor is the last kept id, so the dropped probe (id SIZE+1) is the first row of
    // the next page: `id > SIZE` includes it.
    expect(nextCursor(items, r => r.id)).toBe(CRM_LIST_PAGE_SIZE);
  });

  it('is null for an empty page', () => {
    expect(nextCursor([], (r: { id: number }) => r.id)).toBeNull();
  });
});
