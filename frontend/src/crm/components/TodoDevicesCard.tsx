/**
 * TodoDevicesCard — sign MY devices in to the no-login todo app (#293).
 *
 * The no-login app at /todo opens by per-device browser sessions. A seat makes a
 * one-hour, single-use sign-in code here (QR + link), scans it with the phone, and that
 * browser stays signed in. "Sign out everywhere" ends every one of this seat's devices
 * and cancels its unused codes. Port of cake_os #3764's "Sign in on another device".
 *
 * Member-visible (Personal): every route it drives is `get_current_user` and acts on the
 * caller's own seat. The on/off switch is install-wide and stays on the admin-only Todo
 * mode card; while the app is off this card says so and offers nothing that could fail.
 */

import { useEffect, useState } from 'react';

import { api, ApiError } from '../../core/api/client';
import { copyToClipboard } from '../../shared/hooks/useCopyToClipboard';
import { FONT_SANS, INK, INK_MUTE } from '../../shared/styles';
import { toast } from '../../shared/toast';
import { parseUTC } from '../gtd/util';
import { btnPrimary, btnSecondary } from '../styles';
import { SettingsCard } from './SettingsCard';

interface Devices { enabled: boolean; count: number }
interface SignInLink { url: string; qr: string; expires_at: string }

function devicesLine(count: number): string {
  if (count === 0) return 'No devices signed in yet.';
  return count === 1 ? '1 device signed in.' : `${count} devices signed in.`;
}

export function TodoDevicesCard({ isMobile }: { isMobile: boolean }) {
  const [devices, setDevices] = useState<Devices | null>(null);
  const [link, setLink] = useState<SignInLink | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = () =>
    api<Devices>('/api/crm/gtd/web-sessions').then(setDevices).catch(() => { /* keep unknown */ });

  useEffect(() => { refresh(); }, []);

  async function makeCode() {
    setBusy(true);
    try {
      setLink(await api<SignInLink>('/api/crm/gtd/web-signin-link', { method: 'POST' }));
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        toast.error('The todo app is switched off.');
        setLink(null);
        void refresh();
      } else {
        toast.error('Could not make a sign-in code.');
      }
    } finally {
      setBusy(false);
    }
  }

  async function signOutEverywhere() {
    if (!window.confirm(
      'Sign out everywhere? Every phone and browser you signed in to Todos is signed out, '
      + 'and unused sign-in codes stop working. You stay signed in to CakeCRM here.',
    )) return;
    setBusy(true);
    try {
      await api('/api/crm/gtd/web-signout-all', { method: 'POST' });
      setLink(null);
      setDevices(d => (d ? { ...d, count: 0 } : d));
      toast.success('Signed out everywhere.');
    } catch {
      toast.error('Could not sign out everywhere.');
    } finally {
      setBusy(false);
    }
  }

  async function copy(url: string) {
    if (await copyToClipboard(url)) toast.success('Link copied.');
    else toast.error('Could not copy — select the link and copy it manually.');
  }

  const text: React.CSSProperties = { fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE, lineHeight: 1.6, margin: 0 };

  return (
    <SettingsCard
      id="todo_devices"
      title="Todo app devices"
      description="Open your todos on a phone without logging in to CakeCRM. Each device signs in once and stays signed in."
      isMobile={isMobile}
    >
      {devices === null && <p style={text}>Loading…</p>}

      {devices && !devices.enabled && (
        <p style={text}>
          The no-login todo app is switched off. An admin can turn it on under Settings →
          Assistant → Todo mode.
        </p>
      )}

      {devices?.enabled && (
        <div>
          <p style={{ ...text, color: INK }}>{devicesLine(devices.count)}</p>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
            <button type="button" onClick={() => void makeCode()} disabled={busy} style={{ ...btnPrimary, opacity: busy ? 0.6 : 1 }}>
              {link ? 'Make a new code' : 'Show a sign-in code'}
            </button>
            <button type="button" onClick={() => void signOutEverywhere()} disabled={busy} style={btnSecondary}>
              Sign out everywhere
            </button>
          </div>

          {link && (
            <div style={{ marginTop: 16 }}>
              <img src={link.qr} alt="Sign-in code" width={176} height={176} style={{ display: 'block' }} />
              <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginTop: 10 }}>
                <code style={{
                  flex: '1 1 240px', minWidth: 0, overflowWrap: 'anywhere',
                  fontSize: 12, padding: '6px 8px', borderRadius: 6,
                  background: 'var(--color-ck-bg)', color: 'var(--color-ck-ink)',
                }}>{link.url}</code>
                <button type="button" onClick={() => void copy(link.url)} style={btnSecondary}>Copy</button>
              </div>
              <p style={{ ...text, marginTop: 8 }}>
                Scan it with your phone, or open the link there, and press "Open my list on
                this device". It works once and expires at{' '}
                {parseUTC(link.expires_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}.
                Once the phone is signed in, add the page to its home screen. Anyone who
                opens this code gets your whole todo list, so show it only to your own devices.
              </p>
            </div>
          )}
        </div>
      )}
    </SettingsCard>
  );
}
