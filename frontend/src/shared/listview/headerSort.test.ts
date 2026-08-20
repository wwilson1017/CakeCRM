/**
 * List-view header cycle (the blueprint, relocated in the blueprint).
 *
 * `nextSortState` is the list view's own three-state column-header toggle. The
 * comparator that used to live beside it (`sortRows`) was a second copy of the
 * the blueprint core; the blueprint deleted it in favour of the single `shared/search/sort.ts`
 * comparator, whose semantics stay pinned by `shared/search/sort.test.ts`. What
 * remains to prove here is only the cycle.
 */
import { describe, expect, it } from 'vitest';
import { nextSortState } from './headerSort';

describe('nextSortState', () => {
  it('cycles inactive → asc → desc → cleared', () => {
    const a = nextSortState(null, 'value');
    expect(a).toEqual({ key: 'value', dir: 'asc' });
    const b = nextSortState(a, 'value');
    expect(b).toEqual({ key: 'value', dir: 'desc' });
    expect(nextSortState(b, 'value')).toBeNull();
  });

  it('restarts at ascending when a different column is clicked', () => {
    expect(nextSortState({ key: 'value', dir: 'desc' }, 'name')).toEqual({ key: 'name', dir: 'asc' });
  });
});
