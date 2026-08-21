/**
 * NotificationSettings (issue #6) — a Settings card for Web Push.
 *
 * The toggle's ON state IS the browser subscription (there's no server-side
 * preference store): toggling subscribes/unsubscribes via the native Push API.
 * "Send test notification" exercises the full pipeline with NO AI involved and
 * reports whether Web Push actually reached a device. Keyless.
 *
 * The second toggle (issue #22 Phase 3) controls what the server SENDS rather than
 * where it goes: the daily pipeline digest and the stale-record nudges. That one IS
 * server-side state (heartbeat_state.proactive_enabled), read from the existing
 * /api/heartbeat/status payload so the column has a single source of truth.
 */

import { useEffect, useState } from 'react';
import { api } from '../../core/api/client';
import { toast } from '../../shared/toast';
import { INK_MUTE, FONT_SANS, LINE } from '../../shared/styles';
import { sectionHeading, cardStyle, btnPrimary, btnSecondary } from '../styles';
import {
  isPushSupported, getPushPermissionState, isSubscribed,
  subscribeToPush, unsubscribeFromPush,
} from '../../core/notifications/pushSubscription';

export function NotificationSettings({ isMobile }: { isMobile: boolean }) {
  const [supported] = useState(isPushSupported());
  const [permission, setPermission] = useState<NotificationPermission | 'unsupported'>(() => getPushPermissionState());
  const [subscribed, setSubscribed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  // null = not loaded yet, so the toggle never flashes the wrong state on mount.
  const [proactive, setProactive] = useState<boolean | null>(null);
  const [digestHour, setDigestHour] = useState(8);
  const [proactiveBusy, setProactiveBusy] = useState(false);

  useEffect(() => {
    let active = true;
    isSubscribed().then(v => { if (active) setSubscribed(v); });
    api<{ state: { proactive_enabled?: boolean }; proactive_digest_hour: number }>('/api/heartbeat/status')
      .then(res => {
        if (!active) return;
        setProactive(res.state?.proactive_enabled ?? true);
        setDigestHour(res.proactive_digest_hour ?? 8);
      })
      .catch(() => { /* leave the row hidden rather than guess the server's state */ });
    return () => { active = false; };
  }, []);

  async function toggleProactive() {
    if (proactive === null) return;
    const next = !proactive;
    setProactiveBusy(true);
    try {
      await api('/api/heartbeat/proactive', {
        method: 'POST',
        body: JSON.stringify({ enabled: next }),
      });
      setProactive(next);
      toast.success(next ? 'Daily digest and nudges on.' : 'Daily digest and nudges off.');
    } catch {
      toast.error('Could not change that setting.');
    } finally {
      setProactiveBusy(false);
    }
  }

  async function toggle() {
    setBusy(true);
    try {
      if (subscribed) {
        const ok = await unsubscribeFromPush();
        if (ok) { setSubscribed(false); toast.success('Web Push disabled.'); }
        else toast.error('Could not disable Web Push.');
      } else {
        const ok = await subscribeToPush();
        setPermission(getPushPermissionState());
        if (ok) { setSubscribed(true); toast.success('Web Push enabled.'); }
        else toast.error('Could not enable Web Push. Check your browser permission.');
      }
    } finally {
      setBusy(false);
    }
  }

  async function sendTest() {
    setTesting(true);
    try {
      const res = await api<{ web_push: boolean }>('/api/notifications/test', {
        method: 'POST',
        body: JSON.stringify({ title: 'Test notification', message: 'Push delivery is working.' }),
      });
      if (res.web_push) toast.success('Test push sent to your devices.');
      else toast.success('Logged to your notifications (no push device subscribed).');
    } catch {
      toast.error('Failed to send test notification.');
    } finally {
      setTesting(false);
    }
  }

  return (
    <div style={{ ...cardStyle, padding: isMobile ? 20 : 28, marginTop: 24, maxWidth: 620 }}>
      <div style={sectionHeading()}>Notifications</div>
      <p style={{ fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE, lineHeight: 1.6, margin: '0 0 20px', maxWidth: 460 }}>
        Get browser push notifications when reminders fire and when the assistant has
        something worth flagging. Works with zero AI keys.
      </p>

      {!supported ? (
        <p style={{ fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE }}>
          This browser doesn't support Web Push.
        </p>
      ) : (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <button
              onClick={toggle}
              disabled={busy || permission === 'denied'}
              style={{ ...(subscribed ? btnSecondary : btnPrimary), opacity: busy || permission === 'denied' ? 0.6 : 1 }}
            >
              {busy ? 'Working…' : subscribed ? 'Disable Web Push' : 'Enable Web Push'}
            </button>
            <button
              onClick={sendTest}
              disabled={testing}
              style={{ ...btnSecondary, opacity: testing ? 0.6 : 1 }}
            >
              {testing ? 'Sending…' : 'Send test notification'}
            </button>
          </div>
          {permission === 'denied' && (
            <p style={{ fontFamily: FONT_SANS, fontSize: 12, color: INK_MUTE, margin: '10px 0 0' }}>
              Notifications are blocked for this site in your browser settings — re-enable them there first.
            </p>
          )}
        </>
      )}

      {proactive !== null && (
        <div style={{ marginTop: 24, paddingTop: 20, borderTop: `1px solid ${LINE}` }}>
          <div style={{ fontFamily: FONT_SANS, fontSize: 14, fontWeight: 600, marginBottom: 6 }}>
            Daily digest and nudges
          </div>
          <p style={{ fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE, lineHeight: 1.6, margin: '0 0 14px', maxWidth: 460 }}>
            A pipeline summary each morning around {formatHour(digestHour)}, plus a nudge when a
            deal goes cold or a contact with open business hasn't been spoken to. Works with zero
            AI keys.
          </p>
          <button
            onClick={toggleProactive}
            disabled={proactiveBusy}
            style={{ ...(proactive ? btnSecondary : btnPrimary), opacity: proactiveBusy ? 0.6 : 1 }}
          >
            {proactiveBusy ? 'Working…' : proactive ? 'Turn off' : 'Turn on'}
          </button>
        </div>
      )}
    </div>
  );
}

function formatHour(hour: number): string {
  const h = ((hour % 12) + 12) % 12 || 12;
  return `${h}${hour < 12 ? 'am' : 'pm'} UTC`;
}
