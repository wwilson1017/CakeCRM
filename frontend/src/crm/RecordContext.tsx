// Shared "which CRM record is open right now" context (issue #14).
//
// Provided once by CrmLayout so both the routed pages (<Outlet/>) and the
// AssistantLauncher drawer see it. Surfaces publish via usePublishActiveRecord:
//   • DealDetailBody — while the deal detail is mounted (covers Pipeline + Dashboard,
//     which both render it inside the shared CollectionDetail shell). Deals have no
//     route, so this IS the deal open/close signal.
//   • ContactDetailPage / CompanyDetailPage — type+id derived synchronously from
//     the route param (always current, even mid-load); loaded data supplies only
//     the optional display label.
// Cleanup is OWNERSHIP-based (per-publisher token), not equality-based, so under
// React Strict Mode's double-invoked effects — or the detail shell's keyed remount
// on ‹ › navigation, which is exactly an old publisher unmounting after a new one
// mounted — an older publisher can never clear a newer one's value. Publishers are
// mutually exclusive by routing (only one detail page renders via <Outlet/> at a
// time, and the deal detail is rendered only by Pipeline/Dashboard), so a
// single-entry store suffices — there is never a stack of overlapping records.
//
// `label` is display-only (drawer chip). The wire payload built in
// useAssistantChat carries record_type + record_id exclusively.

import { createContext, useCallback, useContext, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { ActiveRecordContext, ActiveRecordType } from '../assistant';

interface Entry {
  record: ActiveRecordContext;
  token: object;
}

interface RecordContextValue {
  record: ActiveRecordContext | null;
  publish: (record: ActiveRecordContext, token: object) => void;
  clearIfOwner: (token: object) => void;
}

const RecordContext = createContext<RecordContextValue | null>(null);

export function ActiveRecordProvider({ children }: { children: ReactNode }) {
  const [entry, setEntry] = useState<Entry | null>(null);
  const publish = useCallback(
    (record: ActiveRecordContext, token: object) => setEntry({ record, token }),
    [],
  );
  const clearIfOwner = useCallback(
    (token: object) => setEntry((prev) => (prev && prev.token === token ? null : prev)),
    [],
  );
  return (
    <RecordContext.Provider value={{ record: entry?.record ?? null, publish, clearIfOwner }}>
      {children}
    </RecordContext.Provider>
  );
}

export function useActiveRecord(): RecordContextValue {
  const ctx = useContext(RecordContext);
  if (!ctx) throw new Error('useActiveRecord must be used within <ActiveRecordProvider>');
  return ctx;
}

/** Publish a record as "open" while the caller is mounted; ownership-guarded clear
 *  on unmount/change. Pass nulls until the record is known (e.g. while loading). */
export function usePublishActiveRecord(
  recordType: ActiveRecordType | null,
  recordId: number | null | undefined,
  label?: string,
): void {
  const { publish, clearIfOwner } = useActiveRecord();
  const tokenRef = useRef({}); // stable identity per publisher instance
  // Publish in the COMMIT phase (useLayoutEffect), matching the layout-effect record
  // mirror in useAssistantChat — so after a route/sheet change the drawer never reads
  // the previous record in the window before passive effects would have run.
  useLayoutEffect(() => {
    // Guard to the backend's domain: a positive int4 PK. Anything else (NaN,
    // fractional, negative, or out of int4 range) is never published, so it can't
    // reach the wire and can't cause an otherwise-valid chat request to 422.
    if (!recordType || !recordId || !Number.isSafeInteger(recordId) || recordId <= 0 || recordId > 2_147_483_647) return;
    const token = tokenRef.current;
    publish({ recordType, recordId, label }, token);
    return () => clearIfOwner(token);
  }, [recordType, recordId, label, publish, clearIfOwner]);
}
