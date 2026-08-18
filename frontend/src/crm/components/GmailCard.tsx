/**
 * GmailCard — connect a BYO Google account for the assistant's Gmail tools (#8).
 *
 * Read + create-draft only: CakeCRM can never send email. Three states — enter
 * OAuth app credentials (showing the exact redirect URI to register), connect
 * (full-page redirect to Google), and connected (email + disconnect). The OAuth
 * callback lands back on /crm/settings?gmail=connected|error&reason=… for a toast.
 */

import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../../core/api/client';
import { toast } from '../../shared/toast';
import {
  INK, INK_MUTE, CORAL, SAGE, FONT_SANS, FONT_MONO, LINE_STRONG, labelStyle, inputStyle,
} from '../../shared/styles';
import { sectionHeading, cardStyle, btnPrimary, btnSecondary, btnDanger } from '../styles';

interface GmailStatus {
  connected: boolean;
  email: string;
  connection_status: string;
  client_id: string;
  client_secret_present: boolean;
  scopes: string[];
  redirect_uri: string;
}

const REASON_MESSAGES: Record<string, string> = {
  denied: 'You declined the Google consent screen.',
  state: 'The sign-in link expired — click Connect again.',
  no_refresh_token:
    'Google didn’t return offline access. Remove CakeCRM at myaccount.google.com/permissions, then reconnect.',
  scopes: 'Both Gmail permissions are required — reconnect and leave both boxes checked.',
  profile: 'Couldn’t verify the Gmail account — reconnect.',
  exchange: 'Connecting to Google failed — check your client ID/secret and the redirect URI.',
  conflict:
    'The Gmail connection changed while Google was authorizing, so nothing was saved. Click Connect again.',
};

const TRUST_NOTE =
  'CakeCRM can read email and create drafts only — it can never send. Drafts wait in Gmail for your review.';

export function GmailCard({ isMobile }: { isMobile: boolean }) {
  const [status, setStatus] = useState<GmailStatus | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [editingApp, setEditingApp] = useState(false);
  const [busy, setBusy] = useState(false);
  const [params, setParams] = useSearchParams();

  // Reusable fetch for event handlers (after save/disconnect). setState here runs
  // after an await, in a handler — not inside an effect body.
  const refresh = useCallback(async () => {
    try {
      setStatus(await api<GmailStatus>('/api/gmail/status'));
      setLoadError(false);
    } catch {
      setLoadError(true);
    }
  }, []);

  // Initial fetch: setState lives in the promise callback (not synchronously in the
  // effect body), mirroring CrmLayout's setup-status fetch.
  useEffect(() => {
    api<GmailStatus>('/api/gmail/status')
      .then((s) => { setStatus(s); setLoadError(false); })
      .catch(() => setLoadError(true));
  }, []);

  // Callback landing: the OAuth redirect reloads the page fresh, so the initial
  // fetch above already reflects the connected state — here we only toast the
  // result once and strip the params so a later render can't re-toast.
  useEffect(() => {
    const result = params.get('gmail');
    if (!result) return;
    if (result === 'connected') {
      toast.success('Gmail connected.');
    } else {
      const reason = params.get('reason') || '';
      toast.error(REASON_MESSAGES[reason] || 'Couldn’t connect Gmail. Please try again.');
    }
    const next = new URLSearchParams(params);
    next.delete('gmail');
    next.delete('reason');
    setParams(next, { replace: true });
  }, [params, setParams]);

  async function saveApp() {
    const cid = clientId.trim();
    const secret = clientSecret.trim();
    if (!cid || !secret) {
      toast.error('Enter both the client ID and client secret.');
      return;
    }
    setBusy(true);
    try {
      await api('/api/gmail/app', { method: 'POST', body: JSON.stringify({ client_id: cid, client_secret: secret }) });
      setClientSecret('');
      setEditingApp(false);
      toast.success('App credentials saved.');
      await refresh();
    } catch {
      toast.error('Failed to save app credentials.');
    } finally {
      setBusy(false);
    }
  }

  async function connect() {
    setBusy(true);
    try {
      const { auth_url } = await api<{ auth_url: string }>('/api/gmail/oauth/start', { method: 'POST' });
      window.location.assign(auth_url);
    } catch {
      toast.error('Couldn’t start the Google sign-in. Save your app credentials first.');
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    try {
      await api('/api/gmail/connection', { method: 'DELETE' });
      toast.success('Gmail disconnected.');
      await refresh();
    } catch {
      toast.error('Failed to disconnect Gmail.');
    } finally {
      setBusy(false);
    }
  }

  const note: React.CSSProperties = {
    fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE, lineHeight: 1.6, margin: '0 0 20px', maxWidth: 460,
  };
  const fieldWrap: React.CSSProperties = { marginBottom: 16, maxWidth: 460 };

  // A code block showing the exact redirect URI to register in Google Cloud.
  const redirectBlock = (uri: string) => (
    <div style={fieldWrap}>
      <label style={labelStyle}>Redirect URI (add this to your Google OAuth client)</label>
      <code style={{
        display: 'block', fontFamily: FONT_MONO, fontSize: 12, color: INK, background: 'var(--color-ck-bg-raised, #F2EFE9)',
        border: `1px solid ${LINE_STRONG}`, borderRadius: 6, padding: '8px 10px', wordBreak: 'break-all',
      }}>{uri}</code>
    </div>
  );

  function renderBody() {
    if (loadError) {
      return <p style={{ ...note, color: CORAL }}>Couldn’t load the Gmail connection. Reload the page.</p>;
    }
    if (!status) {
      return <p style={note}>Loading…</p>;
    }

    // State 3 — connected.
    if (status.connected) {
      return (
        <div>
          <p style={{ ...note, marginBottom: 12 }}>
            Connected as <strong style={{ color: INK }}>{status.email || 'your Google account'}</strong>.
          </p>
          <p style={{ ...note, marginBottom: 20 }}>{TRUST_NOTE}</p>
          <button onClick={disconnect} disabled={busy} style={{ ...btnDanger, opacity: busy ? 0.6 : 1 }}>
            {busy ? 'Working…' : 'Disconnect Gmail'}
          </button>
        </div>
      );
    }

    // State 1 — no app credentials yet (or editing them).
    if (!status.client_secret_present || editingApp) {
      return (
        <div>
          <p style={note}>
            Bring your own Google OAuth app. In Google Cloud Console → APIs &amp; Services, enable the <strong>Gmail API</strong>,
            create an <strong>OAuth client (Web application)</strong>, and add the redirect URI below. Then paste the client ID and secret.
          </p>
          {redirectBlock(status.redirect_uri)}
          <div style={fieldWrap}>
            <label htmlFor="gmail-client-id" style={labelStyle}>Client ID</label>
            <input id="gmail-client-id" style={inputStyle} value={clientId}
              onChange={e => setClientId(e.target.value)} placeholder="…apps.googleusercontent.com" />
          </div>
          <div style={fieldWrap}>
            <label htmlFor="gmail-client-secret" style={labelStyle}>Client secret</label>
            <input id="gmail-client-secret" type="password" style={inputStyle} value={clientSecret}
              onChange={e => setClientSecret(e.target.value)} placeholder="GOCSPX-…" />
          </div>
          <div style={{ display: 'flex', gap: 12, marginTop: 8 }}>
            <button onClick={saveApp} disabled={busy} style={{ ...btnPrimary, opacity: busy ? 0.6 : 1 }}>
              {busy ? 'Saving…' : 'Save app credentials'}
            </button>
            {editingApp && (
              <button onClick={() => { setEditingApp(false); setClientSecret(''); }} disabled={busy} style={btnSecondary}>
                Cancel
              </button>
            )}
          </div>
        </div>
      );
    }

    // State 2 — app credentials saved, not connected.
    return (
      <div>
        <p style={note}>{TRUST_NOTE}</p>
        {status.connection_status === 'broken' && (
          <p style={{ ...note, color: CORAL }}>Your Gmail connection expired — reconnect below.</p>
        )}
        <p style={{ ...note, marginBottom: 16 }}>
          App configured (<code style={{ fontFamily: FONT_MONO, fontSize: 12 }}>{status.client_id}</code>).
        </p>
        <div style={{ display: 'flex', gap: 12 }}>
          <button onClick={connect} disabled={busy} style={{ ...btnPrimary, opacity: busy ? 0.6 : 1 }}>
            {busy ? 'Redirecting…' : 'Connect Gmail'}
          </button>
          <button onClick={() => { setEditingApp(true); setClientId(status.client_id); }} disabled={busy} style={btnSecondary}>
            Edit app credentials
          </button>
        </div>
      </div>
    );
  }

  return (
    <div style={{ ...cardStyle, padding: isMobile ? 20 : 28, marginTop: 24, maxWidth: 620 }}>
      <div style={{ ...sectionHeading(), display: 'flex', alignItems: 'center', gap: 8 }}>
        Gmail
        {status?.connected && <span style={{ fontFamily: FONT_SANS, fontSize: 12, color: SAGE }}>• connected</span>}
      </div>
      {renderBody()}
    </div>
  );
}
