/**
 * The Owner facet's options, shared by all three CRM list pages (issue #77).
 *
 * Its own module rather than a helper in `useUsers.ts` or `collectionConfig.ts`: the
 * derivation needs the roster (useUsers) AND the facet builder (collectionConfig), and
 * collectionConfig already imports from useUsers — so putting the hook in either one
 * would close a module cycle.
 */
import { useMemo } from 'react';

import { useAuth } from '../core/auth/AuthContext';
import type { FacetOption } from '../shared/search';
import { buildOwnerOptions } from './collectionConfig';
import { useUsers } from './useUsers';

/**
 * `options` is null on a single-seat install, meaning "declare no Owner facet at all".
 *
 * Memoized because a collection config must be referentially stable — an options array
 * with a fresh identity each render would rebuild the config, and with it the search
 * index for the whole corpus, on every keystroke.
 */
export function useOwnerOptions(): { options: FacetOption[] | null; loading: boolean } {
  const { users, loading } = useUsers();
  const { currentUser } = useAuth();
  const meId = currentUser?.id ?? null;
  const options = useMemo(() => buildOwnerOptions(users, meId), [users, meId]);
  return { options, loading };
}
