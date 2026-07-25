import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../core/api/client';
import type { FieldProvenance } from '../core/types';

// Fetches a deal/contact's live-badge provenance (issue #16), keyed by field_name. Mirrors
// NotesThread's fetch idiom: a monotonic reqRef guard so a slow response for a since-switched
// entity can't paint, and a queueMicrotask-deferred effect (react-hooks/set-state-in-effect).
// Pass entityId as null while the entity is still loading — the hook is a no-op then, so it
// can be called unconditionally at the top of a component (before any early return).
export function useProvenance(entityType: 'deal' | 'contact', entityId: number | null) {
  const [byField, setByField] = useState<Record<string, FieldProvenance>>({});
  const [confirming, setConfirming] = useState<string | null>(null);
  const reqRef = useRef(0);

  const refresh = useCallback(async () => {
    const reqId = ++reqRef.current;
    if (entityId == null) {
      setByField({});
      return;
    }
    try {
      const data = await api<{ provenance: FieldProvenance[] }>(
        `/api/crm/provenance/${entityType}/${entityId}`,
      );
      if (reqId !== reqRef.current) return;
      const map: Record<string, FieldProvenance> = {};
      for (const r of data.provenance) map[r.field_name] = r;
      setByField(map);
    } catch {
      if (reqId === reqRef.current) setByField({});
    }
  }, [entityType, entityId]);

  // Clear the previous entity's badges before the reload paints, then refetch. The clear is
  // in the effect (not refresh) so a manual refresh() after a save doesn't flash empty.
  useEffect(() => {
    queueMicrotask(() => {
      setByField({});
      refresh();
    });
  }, [refresh]);

  const confirm = useCallback(
    async (fieldName: string) => {
      if (entityId == null) return;
      setConfirming(fieldName);
      try {
        const res = await api<{ confirmed: boolean; stale?: boolean }>(
          `/api/crm/provenance/${entityType}/${entityId}/confirm`,
          { method: 'POST', body: JSON.stringify({ field_name: fieldName }) },
        );
        // Confirmed OR stale (edited since the AI wrote it — already dead server-side): drop
        // the badge either way. Any other error leaves it in place for a retry.
        if (res.confirmed || res.stale) {
          // Invalidate any in-flight refresh() whose server read predates this confirm, so a
          // late response can't repopulate the badge we just cleared (reqRef also sequences
          // refresh-vs-refresh — this extends it to confirm-vs-refresh).
          reqRef.current += 1;
          setByField(prev => {
            const next = { ...prev };
            delete next[fieldName];
            return next;
          });
        }
      } catch {
        /* leave the badge — retryable */
      } finally {
        setConfirming(null);
      }
    },
    [entityType, entityId],
  );

  return { byField, confirm, confirming, refresh };
}
