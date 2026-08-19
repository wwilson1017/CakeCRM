/**
 * NotificationsBell (issue #6) — the in-app notification + alert surface.
 *
 * Renders in the CRM header (desktop AND mobile). The numeric badge counts ACTIVE
 * NOTIFICATIONS ONLY (alerts are shown as a separate dropdown section, never summed
 * into the badge — every failure alert also delivers a notification row, so summing
 * would double-count the same event). Polls the count every 60s and refetches the
 * full lists when the dropdown opens. Keyless — works with zero AI keys.
 */

import { useEffect, useRef, useState } from 'react';
import { api } from '../../core/api/client';
import { INK, INK_MUTE, INK_SOFT, LINE, LINE_STRONG, BG_CARD, ACCENT, CORAL, FONT_SANS, ACCENT_INK, SHADOW } from '../../shared/styles';

interface NotificationRow {
  id: string; title: string; message: string; created_at: string; channels_sent: string[];
}
interface AlertRow {
  id: string; title: string; message: string; source: string; created_at: string;
}

function relTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 60) return 'just now';
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

function BellIcon({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={1.85} strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
      <path d="M13.73 21a2 2 0 0 1-3.46 0" />
    </svg>
  );
}

export function NotificationsBell() {
  const [open, setOpen] = useState(false);
  const [notifications, setNotifications] = useState<NotificationRow[]>([]);
  const [alerts, setAlerts] = useState<AlertRow[]>([]);
  const [count, setCount] = useState(0);
  const [alertCount, setAlertCount] = useState(0);
  const wrapRef = useRef<HTMLDivElement>(null);

  // Poll the active notification + alert counts for the badge/indicator (setState
  // only in async .then callbacks, never synchronously in the effect body). Alerts
  // are polled too so the "something is wrong" color fires proactively, not only
  // after the dropdown has been opened once.
  useEffect(() => {
    let active = true;
    const poll = () => {
      // True (unbounded) active count for the badge — the list endpoint is capped,
      // so its length would under-report once there are many notifications.
      api<{ count: number }>('/api/notifications/counts')
        .then(res => { if (active) setCount(res.count); })
        .catch(() => { /* keep last */ });
      api<{ count: number }>('/api/alerts/counts')
        .then(res => { if (active) setAlertCount(res.count); })
        .catch(() => { /* keep last */ });
    };
    poll();
    const t = setInterval(poll, 60000);
    return () => { active = false; clearInterval(t); };
  }, []);

  // Load full lists when the dropdown opens.
  useEffect(() => {
    if (!open) return;
    let active = true;
    Promise.all([
      api<{ notifications: NotificationRow[] }>('/api/notifications?status=active&limit=20'),
      api<{ alerts: AlertRow[] }>('/api/alerts?status=active&limit=20'),
    ])
      .then(([n, a]) => {
        if (!active) return;
        setNotifications(n.notifications);
        setAlerts(a.alerts);
        setAlertCount(a.alerts.length);
        // Badge count comes from the unbounded /counts poll, not this capped list.
      })
      .catch(() => { /* best-effort */ });
    return () => { active = false; };
  }, [open]);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [open]);

  async function dismiss(id: string) {
    setNotifications(list => list.filter(n => n.id !== id));
    setCount(c => Math.max(0, c - 1));
    try { await api(`/api/notifications/${id}/dismiss`, { method: 'POST' }); } catch { /* refetch below */ }
  }
  async function dismissAll() {
    setNotifications([]);
    setCount(0);
    try { await api('/api/notifications/dismiss-all', { method: 'POST' }); } catch { /* ignore */ }
  }
  async function actOnAlert(id: string, action: 'acknowledge' | 'resolve') {
    setAlerts(list => list.filter(a => a.id !== id));
    setAlertCount(c => Math.max(0, c - 1));
    try { await api(`/api/alerts/${id}/${action}`, { method: 'POST' }); } catch { /* ignore */ }
  }

  return (
    <div ref={wrapRef} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(o => !o)}
        aria-label="Notifications"
        style={{
          position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'center',
          width: 34, height: 32, border: `1px solid ${LINE_STRONG}`, borderRadius: 6,
          background: 'transparent', color: alertCount > 0 ? CORAL : INK_MUTE, cursor: 'pointer',
        }}
      >
        <BellIcon />
        {count > 0 && (
          <span style={{
            position: 'absolute', top: -6, right: -6, minWidth: 16, height: 16, padding: '0 4px',
            borderRadius: 8, background: ACCENT, color: ACCENT_INK, fontSize: 10, fontWeight: 600,
            fontFamily: FONT_SANS, display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>{count > 99 ? '99+' : count}</span>
        )}
      </button>

      {open && (
        <div style={{
          position: 'absolute', top: 40, right: 0, width: 340, maxWidth: '90vw',
          maxHeight: 460, overflowY: 'auto', background: BG_CARD, border: `1px solid ${LINE}`,
          borderRadius: 8, boxShadow: `0 8px 28px ${SHADOW}`, zIndex: 60,
        }}>
          {/* Alerts section (only when present) */}
          {alerts.length > 0 && (
            <div style={{ borderBottom: `1px solid ${LINE}` }}>
              <div style={{ padding: '10px 14px 4px', fontFamily: FONT_SANS, fontSize: 11,
                fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', color: CORAL }}>
                Alerts
              </div>
              {alerts.map(a => (
                <div key={a.id} style={{ padding: '8px 14px 12px' }}>
                  <div style={{ fontFamily: FONT_SANS, fontSize: 13, fontWeight: 600, color: INK }}>{a.title}</div>
                  <div style={{ fontFamily: FONT_SANS, fontSize: 12, color: INK_MUTE, lineHeight: 1.5, margin: '2px 0 6px' }}>{a.message}</div>
                  <div style={{ display: 'flex', gap: 10 }}>
                    <button onClick={() => actOnAlert(a.id, 'acknowledge')} style={linkBtn}>Acknowledge</button>
                    <button onClick={() => actOnAlert(a.id, 'resolve')} style={linkBtn}>Resolve</button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Notifications */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px 6px' }}>
            <span style={{ fontFamily: FONT_SANS, fontSize: 11, fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', color: INK_SOFT }}>Notifications</span>
            {notifications.length > 0 && <button onClick={dismissAll} style={linkBtn}>Dismiss all</button>}
          </div>
          {notifications.length === 0 ? (
            <div style={{ padding: '8px 14px 18px', fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE }}>
              You're all caught up.
            </div>
          ) : notifications.map(n => (
            <div key={n.id} style={{ display: 'flex', gap: 8, padding: '8px 14px', borderTop: `1px solid ${LINE}` }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontFamily: FONT_SANS, fontSize: 13, fontWeight: 600, color: INK }}>{n.title}</div>
                <div style={{ fontFamily: FONT_SANS, fontSize: 12, color: INK_MUTE, lineHeight: 1.5, margin: '2px 0 3px', whiteSpace: 'pre-wrap' }}>{n.message}</div>
                <div style={{ fontFamily: FONT_SANS, fontSize: 11, color: INK_SOFT }}>{relTime(n.created_at)}</div>
              </div>
              <button onClick={() => dismiss(n.id)} aria-label="Dismiss" style={{ ...linkBtn, color: INK_SOFT, alignSelf: 'flex-start' }}>✕</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const linkBtn: React.CSSProperties = {
  background: 'transparent', border: 'none', padding: 0, cursor: 'pointer',
  fontFamily: FONT_SANS, fontSize: 12, color: ACCENT,
};
