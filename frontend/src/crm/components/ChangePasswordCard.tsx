/**
 * ChangePasswordCard — change the login password from Settings (#78).
 *
 * Self-contained settings card. The password lives in the DB-backed credential, so
 * a user on a hosted instance can rotate it without the operator touching env vars.
 * When 2FA is enabled the backend also demands a TOTP (or backup) code, so the card
 * fetches /api/auth/2fa/status to know whether to ask for one — `null` means the
 * status hasn't resolved yet, and the form stays disabled until it does rather than
 * assuming 2FA is off and submitting an incomplete request.
 *
 * On success the backend returns a fresh token, which goes through the auth context
 * so other tabs are told about it instead of holding one that's about to be replaced.
 * No AI involvement — this works with zero provider keys, like the rest of the CRM.
 */

import { useEffect, useState } from 'react';

import { api } from '../../core/api/client';
import { useAuth } from '../../core/auth/AuthContext';
import { CORAL_TEXT, FONT_SANS, INK_MUTE, labelStyle, inputStyle } from '../../shared/styles';
import { toast } from '../../shared/toast';
import { btnPrimary } from '../styles';
import { SettingsCard } from './SettingsCard';

// Mirrors MIN_PASSWORD_LENGTH in backend/core/auth.py — the server stays authoritative.
const MIN_PASSWORD_LENGTH = 8;

interface TwoFactorStatus {
  enabled: boolean;
}

interface TokenResponse {
  access_token: string;
}

export function ChangePasswordCard({ isMobile }: { isMobile: boolean }) {
  const { applyToken } = useAuth();
  const [twoFactorEnabled, setTwoFactorEnabled] = useState<boolean | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [code, setCode] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    api<TwoFactorStatus>('/api/auth/2fa/status')
      .then((s) => setTwoFactorEnabled(s.enabled))
      .catch(() => setLoadError(true));
  }, []);

  const loaded = twoFactorEnabled !== null;
  const mismatch = confirm.length > 0 && next !== confirm;
  const canSubmit =
    loaded && !saving && current.length > 0 && next.length >= MIN_PASSWORD_LENGTH &&
    next === confirm && (!twoFactorEnabled || code.trim().length > 0);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSaving(true);
    setError('');
    try {
      const res = await api<TokenResponse>('/api/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({
          current_password: current,
          new_password: next,
          ...(twoFactorEnabled ? { code: code.trim() } : {}),
        }),
      });
      applyToken(res.access_token);
      setCurrent('');
      setNext('');
      setConfirm('');
      setCode('');
      toast.success('Password changed.');
    } catch (err) {
      // The backend answers a wrong current password with 400, not 401, so it
      // surfaces here instead of bouncing the user to /login via the api() wrapper.
      setError(err instanceof Error ? err.message.replace(/^API error \d+: /, '') : 'Failed to change password.');
    } finally {
      setSaving(false);
    }
  }

  const fieldWrap: React.CSSProperties = { marginBottom: 20, maxWidth: 420 };

  return (
    <SettingsCard
      id="change_password"
      title="Change password"
      description={`Update the password you use to sign in. Must be at least ${MIN_PASSWORD_LENGTH} characters.`}
      isMobile={isMobile}
    >
      {loadError && (
        // The form stays disabled: submitting without knowing whether 2FA is on would
        // fail server-side anyway, and silently assuming it's off is the riskier guess.
        <p style={{
          fontFamily: FONT_SANS, fontSize: 13, color: CORAL_TEXT, lineHeight: 1.5,
          margin: '0 0 20px', maxWidth: 460,
        }}>
          Couldn't check your two-factor status, so changing your password is
          unavailable right now. Reload the page to try again.
        </p>
      )}

      <form onSubmit={handleSubmit}>
        <div style={fieldWrap}>
          <label htmlFor="current-password" style={labelStyle}>Current password</label>
          <input
            id="current-password"
            type="password"
            autoComplete="current-password"
            style={inputStyle}
            value={current}
            disabled={!loaded || saving}
            onChange={e => setCurrent(e.target.value)}
          />
        </div>

        <div style={fieldWrap}>
          <label htmlFor="new-password" style={labelStyle}>New password</label>
          <input
            id="new-password"
            type="password"
            autoComplete="new-password"
            style={inputStyle}
            value={next}
            disabled={!loaded || saving}
            onChange={e => setNext(e.target.value)}
          />
        </div>

        <div style={fieldWrap}>
          <label htmlFor="confirm-password" style={labelStyle}>Confirm new password</label>
          <input
            id="confirm-password"
            type="password"
            autoComplete="new-password"
            aria-invalid={mismatch}
            style={inputStyle}
            value={confirm}
            disabled={!loaded || saving}
            onChange={e => setConfirm(e.target.value)}
          />
          {mismatch && (
            <p style={{ fontFamily: FONT_SANS, fontSize: 12, color: CORAL_TEXT, margin: '8px 0 0' }}>
              Passwords don't match.
            </p>
          )}
        </div>

        {twoFactorEnabled && (
          <div style={fieldWrap}>
            <label htmlFor="password-2fa-code" style={labelStyle}>Two-factor code</label>
            <input
              id="password-2fa-code"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="123456"
              style={inputStyle}
              value={code}
              disabled={saving}
              onChange={e => setCode(e.target.value)}
            />
            <p style={{ fontFamily: FONT_SANS, fontSize: 12, color: INK_MUTE, margin: '8px 0 0' }}>
              From your authenticator app, or one of your backup codes.
            </p>
          </div>
        )}

        {error && (
          <p role="alert" style={{
            fontFamily: FONT_SANS, fontSize: 13, color: CORAL_TEXT, lineHeight: 1.5,
            margin: '0 0 20px', maxWidth: 460,
          }}>
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={!canSubmit}
          style={{ ...btnPrimary, opacity: canSubmit ? 1 : 0.6, cursor: canSubmit ? 'pointer' : 'default' }}
        >{saving ? 'Changing…' : 'Change password'}</button>
      </form>
    </SettingsCard>
  );
}
