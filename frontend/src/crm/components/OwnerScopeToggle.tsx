/**
 * OwnerScopeToggle — the "Everyone / Mine" switch on the list pages (issue #60).
 *
 * "Mine" is nothing more than `owner_id=<me>` on the list request; there is no
 * separate endpoint, flag or magic value, and "Everyone" simply omits the parameter
 * — which is exactly how an install that never assigns owners behaves.
 *
 * The choice persists per list in sessionStorage, following #21's pipeline-filter
 * idiom: a filter that silently resets every time you open a record and come back
 * reads as a bug. Each list passes its own storageKey so Contacts and Tasks don't
 * share one scope.
 *
 * Rendered only when the install has more than one user. On a single-seat install
 * "Mine" and "Everyone" are the same set, so the control would be decoration.
 */

import { useCallback, useState } from 'react';

import { ACCENT, ACCENT_INK, FONT_DISPLAY, INK_MUTE, LINE_STRONG } from '../../shared/styles';
import { useUsers } from '../useUsers';

export function useOwnerScope(storageKey: string) {
  const [mineOnly, setMineOnly] = useState<boolean>(() => {
    try {
      return sessionStorage.getItem(storageKey) === '1';
    } catch {
      return false;
    }
  });

  const set = useCallback(
    (next: boolean) => {
      setMineOnly(next);
      try {
        sessionStorage.setItem(storageKey, next ? '1' : '0');
      } catch {
        /* private-mode / quota: the toggle still works for this session */
      }
    },
    [storageKey],
  );

  return [mineOnly, set] as const;
}

export function OwnerScopeToggle({
  mineOnly,
  onChange,
}: {
  mineOnly: boolean;
  onChange: (mineOnly: boolean) => void;
}) {
  const { users } = useUsers();

  // One seat means the two options select the same records, so the control would be
  // decoration. Derived, not state — it is a pure function of the roster.
  if (users.length <= 1) return null;

  const base: React.CSSProperties = {
    fontFamily: FONT_DISPLAY,
    fontSize: 13,
    padding: '6px 12px',
    border: `1px solid ${LINE_STRONG}`,
    cursor: 'pointer',
    background: 'transparent',
    color: INK_MUTE,
  };
  const active: React.CSSProperties = { ...base, background: ACCENT, color: ACCENT_INK };

  return (
    <div role="group" aria-label="Owner scope" style={{ display: 'inline-flex' }}>
      <button
        type="button"
        aria-pressed={!mineOnly}
        onClick={() => onChange(false)}
        style={{ ...(mineOnly ? base : active), borderRadius: '4px 0 0 4px' }}
      >
        Everyone
      </button>
      <button
        type="button"
        aria-pressed={mineOnly}
        onClick={() => onChange(true)}
        style={{ ...(mineOnly ? active : base), borderRadius: '0 4px 4px 0', borderLeft: 'none' }}
      >
        Mine
      </button>
    </div>
  );
}
