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
 * The third part (#360) controls Baker's background check-in, also server-side state.
 */

import { useEffect, useState } from 'react';
import { api, ApiError } from '../../core/api/client';
import { useAuth } from '../../core/auth/AuthContext';
import { toast } from '../../shared/toast';
import { INK_MUTE, FONT_SANS, LINE, inputStyle, labelStyle } from '../../shared/styles';
import { btnPrimary, btnSecondary, settingsSubheading } from '../styles';
import { parseUTC } from '../gtd/util';
import { SettingsCard } from './SettingsCard';
import {
  isPushSupported, getPushPermissionState, isSubscribed,
  subscribeToPush, unsubscribeFromPush,
} from '../../core/notifications/pushSubscription';

type HeartbeatSettings = {
  enabled: boolean;
  interval_minutes: number;
  quiet_hours: string;
  min_gap_hours: number;
  paused_until: string | null;
  timezone: string;
  operator_enabled: boolean;
};
const INTERVALS = [30, 60, 120, 240, 480, 720, 1440];
const GAPS = [0, 1, 2, 4, 8, 12, 24];

export function NotificationSettings({ isMobile }: { isMobile: boolean }) {
  // Web Push configures this browser. Digest and check-in blocks configure the install
  // through admin-only routes, inside this member-visible card.
  const { isAdmin } = useAuth();
  const [supported] = useState(isPushSupported());
  const [permission, setPermission] = useState<NotificationPermission | 'unsupported'>(() => getPushPermissionState());
  const [subscribed, setSubscribed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  // null = not loaded yet, so the toggle never flashes the wrong state on mount.
  const [proactive, setProactive] = useState<boolean | null>(null);
  const [digestHour, setDigestHour] = useState(8);
  const [proactiveBusy, setProactiveBusy] = useState(false);

  const [hb, setHb] = useState<HeartbeatSettings | null>(null);
  const [providerReady, setProviderReady] = useState(false);
  const [intervalMin, setIntervalMin] = useState(30);
  const [quietStart, setQuietStart] = useState('');
  const [quietEnd, setQuietEnd] = useState('');
  const [gap, setGap] = useState(4);
  const [hbBusy, setHbBusy] = useState(false);

  function applyHb(s: HeartbeatSettings) {
    setHb(s);
    setIntervalMin(s.interval_minutes);
    const [start, end] = s.quiet_hours === 'off' ? ['', ''] : s.quiet_hours.split('-');
    setQuietStart(start);
    setQuietEnd(end);
    setGap(s.min_gap_hours);
  }

  async function saveHb(body: Record<string, boolean | number | string>) {
    setHbBusy(true);
    try {
      const res = await api<{ settings: HeartbeatSettings }>('/api/heartbeat/settings', {
        method: 'PUT', body: JSON.stringify(body),
      });
      applyHb(res.settings);
      toast.success('Check-in settings saved.');
    } catch (e) {
      toast.error(e instanceof ApiError ? e.detail : 'Could not save those settings.');
    } finally {
      setHbBusy(false);
    }
  }

  function saveSchedule() {
    if (Boolean(quietStart) !== Boolean(quietEnd)) {
      toast.error('Set both a start and an end for quiet hours, or clear both.');
      return;
    }
    // Send the interval only when it changed: an operator's env default outside
    // 30-1440 is shown as-is and must not make every Save fail.
    void saveHb({
      ...(hb && intervalMin === hb.interval_minutes ? {} : { interval_minutes: intervalMin }),
      quiet_hours: quietStart ? `${quietStart}-${quietEnd}` : 'off', min_gap_hours: gap });
  }

  useEffect(() => {
    let active = true;
    isSubscribed().then(v => { if (active) setSubscribed(v); });
    api<{ state: { proactive_enabled?: boolean }; proactive_digest_hour: number;
      provider_ready?: boolean; heartbeat_settings?: HeartbeatSettings }>('/api/heartbeat/status')
      .then(res => {
        if (!active) return;
        setProactive(res.state?.proactive_enabled ?? true);
        setDigestHour(res.proactive_digest_hour ?? 8);
        setProviderReady(res.provider_ready ?? false);
        if (res.heartbeat_settings) applyHb(res.heartbeat_settings);
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
    <SettingsCard
      id="notifications"
      title="Notifications"
      description="Get browser push notifications when the assistant has something worth flagging. Works with zero AI keys."
      isMobile={isMobile}
    >
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

      {isAdmin && proactive !== null && (
        <div style={{ marginTop: 24, paddingTop: 20, borderTop: `1px solid ${LINE}` }}>
          <h3 style={settingsSubheading}>Daily digest and nudges</h3>
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
      {isAdmin && providerReady && hb !== null && (
        <div style={{ marginTop: 24, paddingTop: 20, borderTop: `1px solid ${LINE}` }}>
          <h3 style={settingsSubheading}>Baker's background check-in</h3>
          <p style={{ fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE, lineHeight: 1.6, maxWidth: 460 }}>
            Baker looks for newly overdue todos, deals going cold and contacts nobody has followed
            up with, and sends one notification when something is new. It does not repeat what it
            has already told you. Needs an AI provider. Separate from the daily digest above.
          </p>
          {!hb.operator_enabled && (
            <p style={{ color: INK_MUTE, fontSize: 13 }}>
              Background check-ins are switched off for this server (HEARTBEAT_ENABLED).
              These settings take effect once it is on.
            </p>
          )}
          {hb.paused_until && (
            <p style={{ color: INK_MUTE, fontSize: 13 }}>
              Paused until {parseUTC(hb.paused_until).toLocaleString()}{' '}
              <button style={btnSecondary} disabled={hbBusy} onClick={() => saveHb({ pause_days: 0 })}>Resume</button>
            </p>
          )}
          <button style={hb.enabled ? btnSecondary : btnPrimary} disabled={hbBusy}
            onClick={() => saveHb({ enabled: !hb.enabled })}>
            {hb.enabled ? 'Turn off' : 'Turn on'}
          </button>
          <div style={{ display: 'grid', gap: 16, marginTop: 16, maxWidth: 460 }}>
            <label style={labelStyle}>
              Check every
              <select style={inputStyle} value={intervalMin} disabled={hbBusy}
                onChange={e => setIntervalMin(Number(e.target.value))}>
                {!INTERVALS.includes(intervalMin) && <option value={intervalMin}>{intervalMin} minutes</option>}
                {INTERVALS.map(n => <option key={n} value={n}>
                  {n === 30 ? '30 minutes' : n === 1440 ? 'Once a day' : `${n / 60} ${n === 60 ? 'hour' : 'hours'}`}
                </option>)}
              </select>
            </label>
            <div>
              <div style={labelStyle}>Quiet hours ({hb.timezone} time)</div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <input type="time" aria-label="Quiet hours start" style={{ ...inputStyle, width: 'auto' }}
                  value={quietStart} disabled={hbBusy} onChange={e => setQuietStart(e.target.value)} />
                <span style={{ color: INK_MUTE }}>to</span>
                <input type="time" aria-label="Quiet hours end" style={{ ...inputStyle, width: 'auto' }}
                  value={quietEnd} disabled={hbBusy} onChange={e => setQuietEnd(e.target.value)} />
                <button disabled={hbBusy} onClick={() => { setQuietStart(''); setQuietEnd(''); }}
                  style={{ background: 'none', border: 'none', padding: 0, color: INK_MUTE, cursor: 'pointer', textDecoration: 'underline' }}>Clear</button>
              </div>
              <p style={{ color: INK_MUTE, fontSize: 12 }}>
                No check-ins between these times. The window can cross midnight.
              </p>
            </div>
            <label style={labelStyle}>
              At most one notification every
              <select style={inputStyle} value={gap} disabled={hbBusy} onChange={e => setGap(Number(e.target.value))}>
                {!GAPS.includes(gap) && <option value={gap}>{gap} hours</option>}
                {GAPS.map(n => <option key={n} value={n}>
                  {n === 0 ? 'No minimum' : `${n} ${n === 1 ? 'hour' : 'hours'}`}
                </option>)}
              </select>
            </label>
            <div><button style={btnPrimary} disabled={hbBusy} onClick={saveSchedule}>
              {hbBusy ? 'Saving…' : 'Save'}
            </button></div>
          </div>
        </div>
      )}
    </SettingsCard>
  );
}

function formatHour(hour: number): string {
  const h = ((hour % 12) + 12) % 12 || 12;
  return `${h}${hour < 12 ? 'am' : 'pm'} UTC`;
}
