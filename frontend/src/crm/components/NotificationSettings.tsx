/**
 * NotificationSettings (issue #6) — a Settings card for Web Push.
 *
 * The toggle's ON state IS the browser subscription (there's no server-side
 * preference store): toggling subscribes/unsubscribes via the native Push API.
 * "Send test notification" exercises the full pipeline with NO AI involved and
 * reports whether Web Push actually reached a device. Keyless.
 */

import { useEffect, useState } from 'react';
import { api } from '../../core/api/client';
import { toast } from '../../shared/toast';
import { INK_MUTE, FONT_SANS } from '../../shared/styles';
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

  useEffect(() => {
    let active = true;
    isSubscribed().then(v => { if (active) setSubscribed(v); });
    return () => { active = false; };
  }, []);

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
    </div>
  );
}
