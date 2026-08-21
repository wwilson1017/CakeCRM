import { useState, useEffect } from 'react';

/**
 * The one debounce hook.
 *
 * Three behaviourally identical copies of this used to live in `apps/crm/hooks/`,
 * `two sibling apps' hook folders — the kanban copy's own docstring said
 * "per-app copy of the CRM hook — there is no shared hooks home in this repo", which was
 * already false (`core/hooks/` predates it). The copies were unavoidable once
 * `shared/search/SearchInput` needed one, since a shared component may not import from
 * `apps/*`, so consolidating here was a precondition of the shared search bar rather than a
 * tidy-up alongside it.
 *
 * `delay = 0` still round-trips through `setTimeout` + `useState` + an effect, so it is NOT a
 * synchronous escape hatch. A caller that needs the value on the same tick must branch before
 * the hook rather than passing 0.
 */
export function useDebounce<T>(value: T, delay = 250): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);

  return debounced;
}
