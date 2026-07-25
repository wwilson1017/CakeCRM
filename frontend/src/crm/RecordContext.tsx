// Shared "which CRM record is open right now" context (issue #14).
//
// Provided once by CrmLayout so both the routed pages (<Outlet/>) and the
// AssistantLauncher drawer see it. Surfaces publish via usePublishActiveRecord:
//   • DealDetailSheet — while the sheet is mounted (covers Pipeline + Dashboard
//     with zero edits to either page).
//   • ContactDetailPage / CompanyDetailPage — type+id derived synchronously from
//     the route param (always current, even mid-load); loaded data supplies only
//     the optional display label.
// Cleanup is OWNERSHIP-based (per-publisher token), not equality-based, so under
// React Strict Mode's double-invoked effects — or two publishers of the same
// record — an older publisher can never clear a newer one's value.
//
// `label` is display-only (drawer chip). The wire payload built in
// useAssistantChat carries record_type + record_id exclusively.

import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
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
  useEffect(() => {
    if (!recordType || !recordId || !Number.isFinite(recordId)) return;
    const token = tokenRef.current;
    publish({ recordType, recordId, label }, token);
    return () => clearIfOwner(token);
  }, [recordType, recordId, label, publish, clearIfOwner]);
}
