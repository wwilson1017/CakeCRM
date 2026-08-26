/**
 * useUsers — the install's user roster (issue #60).
 *
 * One module-level cache shared by every consumer: owner dropdowns on four entity
 * forms, the "Mine / Everyone" toggles, the pipeline owner facet and the per-rep
 * dashboard table all need to turn an owner_id into a human name, and they render
 * on the same screens. Fetching per component would mean half a dozen identical
 * requests on a page load.
 *
 * GET /api/users is readable by any authenticated user, not just admins — hiding
 * the roster would only mean members see bare numeric ids next to records whose
 * ownership is already visible.
 *
 * INACTIVE users are kept in the list on purpose. A departed rep still owns records
 * and still appears in historical attribution, so their name has to resolve;
 * `activeUsers` is the subset the pickers offer, so you cannot newly assign work to
 * a deactivated account through the UI.
 */

import { useCallback, useEffect, useState } from 'react';

import { api } from '../core/api/client';

export interface CrmUser {
  id: number;
  email: string;
  name: string;
  role: 'admin' | 'member';
  is_active: boolean;
}

export const UNASSIGNED_LABEL = 'Unassigned';

let cache: CrmUser[] | null = null;
let inFlight: Promise<CrmUser[]> | null = null;
const subscribers = new Set<(users: CrmUser[]) => void>();

function fetchUsers(): Promise<CrmUser[]> {
  // Single-flight: several components mounting together share one request.
  if (inFlight) return inFlight;
  inFlight = api<{ users: CrmUser[] }>('/api/users')
    .then(res => {
      cache = res.users ?? [];
      subscribers.forEach(fn => fn(cache!));
      return cache;
    })
    .finally(() => {
      inFlight = null;
    });
  return inFlight;
}

/** Drop the cache so the next read refetches — call after adding or editing a user. */
export function invalidateUsers(): void {
  cache = null;
  fetchUsers().catch(() => {
    /* a failed refresh leaves consumers on their last good list */
  });
}

export function useUsers() {
  const [users, setUsers] = useState<CrmUser[]>(cache ?? []);
  const [loading, setLoading] = useState(cache === null);

  useEffect(() => {
    subscribers.add(setUsers);
    if (cache === null) {
      fetchUsers()
        .catch(() => {
          /* Non-fatal: owner pickers fall back to showing ids. */
        })
        .finally(() => setLoading(false));
    }
    return () => {
      subscribers.delete(setUsers);
    };
  }, []);

  const nameFor = useCallback(
    (ownerId: number | null | undefined): string => {
      if (ownerId === null || ownerId === undefined) return UNASSIGNED_LABEL;
      const user = users.find(u => u.id === ownerId);
      if (!user) return `User ${ownerId}`;
      return user.name.trim() || user.email;
    },
    [users],
  );

  return {
    users,
    activeUsers: users.filter(u => u.is_active),
    loading,
    nameFor,
  };
}

