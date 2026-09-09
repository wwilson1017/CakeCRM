/**
 * CakeCRM — toast viewport. Mounted once in App.tsx; renders the toast store
 * as a fixed bottom-right stack (full-width on mobile), kept clear of the CRM shell's
 * "Ask Baker" pill: the pill is 52px tall and 24px off the bottom-right corner, so the
 * stack starts at 88px (24 + 52 + 12 clearance — the same number `crm/styles`'
 * LAUNCHER_CLEARANCE_PX carries, repeated rather than imported because nothing in
 * `shared/` may depend on `crm/`). Toasts and the pill must never share a corner: a
 * toast that lands on the launcher hides the one control that is always on screen.
 */

import { useCallback, useEffect, useRef, useSyncExternalStore } from 'react';
import { subscribeToasts, getToasts, dismissToast } from './toast';
import type { ToastItem, ToastSeverity } from './toast';
import { useIsMobile } from './useIsMobile';
import {
  BG_ELEV, LINE_STRONG, INK, INK_DIM, FONT_SANS, mono, SHADOW,
  CORAL_FILL, CORAL_TEXT, SAGE_FILL, SAGE_TEXT, GOLD_FILL, GOLD_TEXT,
} from './styles';

// Two maps rather than one, because a toast paints its severity twice in different roles:
// the 3px left border is a FILL and the "Error"/"Success" tag above the message is TEXT.
// A single value cannot serve both — see the FILL vs TEXT block in index.css (issue #119).
const SEVERITY_FILL: Record<ToastSeverity, string> = {
  error: CORAL_FILL,
  success: SAGE_FILL,
  info: GOLD_FILL,
};

const SEVERITY_TEXT: Record<ToastSeverity, string> = {
  error: CORAL_TEXT,
  success: SAGE_TEXT,
  info: GOLD_TEXT,
};

const SEVERITY_TAG: Record<ToastSeverity, string> = {
  error: 'Error',
  success: 'Success',
  info: 'Notice',
};

// getServerSnapshot must return a stable reference, or React warns about
// an infinite loop of new snapshots.
const EMPTY: ToastItem[] = [];

export function ToastViewport() {
  const items = useSyncExternalStore(subscribeToasts, getToasts, () => EMPTY);
  const isMobile = useIsMobile();

  if (items.length === 0) return null;

  return (
    <div
      style={{
        position: 'fixed',
        zIndex: 200,
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        ...(isMobile
          ? { left: 12, right: 12, bottom: 88 }
          : { right: 20, bottom: 88, width: 360 }),
      }}
    >
      {items.map(item => (
        <ToastCard key={item.id} item={item} />
      ))}
    </div>
  );
}

function ToastCard({ item }: { item: ToastItem }) {
  const fill = SEVERITY_FILL[item.severity];
  const text = SEVERITY_TEXT[item.severity];
  // Auto-dismiss with hover-pause: track remaining time across pauses.
  const remainingRef = useRef(item.duration);
  const startedAtRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stop = useCallback(() => {
    if (timerRef.current !== null) clearTimeout(timerRef.current);
    timerRef.current = null;
  }, []);

  const start = useCallback(() => {
    startedAtRef.current = Date.now();
    timerRef.current = setTimeout(() => dismissToast(item.id), remainingRef.current);
  }, [item.id]);

  useEffect(() => {
    start();
    return stop;
  }, [start, stop]);

  function handleMouseEnter() {
    stop();
    remainingRef.current = Math.max(0, remainingRef.current - (Date.now() - startedAtRef.current));
  }

  return (
    <div
      role={item.severity === 'error' ? 'alert' : 'status'}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={start}
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 10,
        background: BG_ELEV,
        border: `1px solid ${LINE_STRONG}`,
        borderLeft: `3px solid ${fill}`,
        borderRadius: 8,
        padding: '10px 12px 10px 14px',
        boxShadow: `0 8px 32px ${SHADOW}`,
        animation: 'ck-toast-in 0.18s ease-out',
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ ...mono(10, text), marginBottom: 3 }}>{SEVERITY_TAG[item.severity]}</div>
        <div style={{ fontFamily: FONT_SANS, fontSize: 13, color: INK, lineHeight: 1.45, overflowWrap: 'break-word' }}>
          {item.message}
        </div>
      </div>
      <button
        onClick={() => dismissToast(item.id)}
        aria-label="Dismiss"
        style={{
          background: 'none',
          border: 'none',
          color: INK_DIM,
          cursor: 'pointer',
          fontSize: 16,
          lineHeight: 1,
          padding: '2px 4px',
          flexShrink: 0,
        }}
      >
        ×
      </button>
    </div>
  );
}
