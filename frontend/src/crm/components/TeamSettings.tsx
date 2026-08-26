/**
 * TeamSettings — manage the install's user accounts (issue #60).
 *
 * Admin-only, and rendered only for admins — but the gate that matters is the one on
 * the server (`require_admin` on every write route here). This is an affordance, not
 * a permission.
 *
 * There is no delete. Accounts are deactivated, so a departed rep's name keeps
 * rendering on the records they worked and no owner_id is ever left dangling.
 *
 * Password reset is admin-driven because a self-hosted CakeCRM has no mail
 * infrastructure — an email-link reset is not implementable, and none is faked.
 */

import { useState } from 'react';

import { api } from '../../core/api/client';
import { useAuth } from '../../core/auth/AuthContext';
import { CORAL, FONT_SANS, INK_MUTE, INK_SOFT, LINE, labelStyle, inputStyle } from '../../shared/styles';
import { toast } from '../../shared/toast';
import { useUsers, invalidateUsers, type CrmUser } from '../useUsers';
import { btnPrimary, btnSecondary, btnSmall } from '../styles';
import { SettingsCard } from './SettingsCard';

// Mirrors MIN_PASSWORD_LENGTH in backend/core/auth.py — the server stays authoritative.
const MIN_PASSWORD_LENGTH = 8;

export function TeamSettings({ isMobile }: { isMobile: boolean }) {
  const { currentUser, isAdmin } = useAuth();
  const { users, loading } = useUsers();

  const [showInvite, setShowInvite] = useState(false);
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<'admin' | 'member'>('member');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [resettingId, setResettingId] = useState<number | null>(null);
  const [resetPassword, setResetPassword] = useState('');
  const [resetClears2fa, setResetClears2fa] = useState(false);

  // Members don't get a Team card at all. The routes 403 regardless.
  if (!isAdmin) return null;

  async function createUser() {
    setError('');
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    setSaving(true);
    try {
      await api('/api/users', {
        method: 'POST',
        body: JSON.stringify({ email, name, password, role }),
      });
      invalidateUsers();
      setShowInvite(false);
      setEmail(''); setName(''); setPassword(''); setRole('member');
      toast.success('User added');
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Could not add the user');
    } finally {
      setSaving(false);
    }
  }

  async function patchUser(user: CrmUser, changes: Partial<Pick<CrmUser, 'role' | 'is_active'>>) {
    try {
      await api(`/api/users/${user.id}`, {
        method: 'PATCH',
        body: JSON.stringify(changes),
      });
      invalidateUsers();
    } catch (err: unknown) {
      // The last-admin guard lands here, and its message is the useful part.
      toast.error(err instanceof Error ? err.message : 'Could not update the user');
    }
  }

  async function submitReset(userId: number) {
    if (resetPassword.length < MIN_PASSWORD_LENGTH) {
      toast.error(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    try {
      await api(`/api/users/${userId}/password`, {
        method: 'POST',
        body: JSON.stringify({
          new_password: resetPassword,
          clear_two_factor: resetClears2fa,
        }),
      });
      setResettingId(null);
      setResetPassword('');
      setResetClears2fa(false);
      toast.success('Password reset — tell them their new password');
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Could not reset the password');
    }
  }

  const rowStyle: React.CSSProperties = {
    display: 'flex',
    flexDirection: isMobile ? 'column' : 'row',
    alignItems: isMobile ? 'stretch' : 'center',
    gap: 10,
    padding: '10px 0',
    borderTop: `1px solid ${LINE}`,
    fontFamily: FONT_SANS,
  };

  return (
    <SettingsCard
      id="team"
      title="Team"
      description="Everyone here shares one CRM. Records carry an owner so you can filter to your own, but ownership is not a permission — any member can see and edit anything."
      isMobile={isMobile}
    >
      {loading ? (
        <p style={{ color: INK_SOFT, fontSize: 13, fontFamily: FONT_SANS }}>Loading…</p>
      ) : (
        <div>
          {users.map(u => (
            <div key={u.id} style={rowStyle}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, color: u.is_active ? undefined : INK_SOFT }}>
                  {u.name.trim() || u.email}
                  {u.id === currentUser?.id && (
                    <span style={{ color: INK_SOFT, fontSize: 12 }}> — you</span>
                  )}
                  {!u.is_active && (
                    <span style={{ color: INK_SOFT, fontSize: 12 }}> — deactivated</span>
                  )}
                </div>
                <div style={{ fontSize: 12, color: INK_MUTE }}>{u.email}</div>
              </div>

              <select
                aria-label={`Role for ${u.email}`}
                value={u.role}
                disabled={u.id === currentUser?.id}
                onChange={e => patchUser(u, { role: e.target.value as 'admin' | 'member' })}
                style={{ ...inputStyle, width: isMobile ? '100%' : 130, marginBottom: 0 }}
              >
                <option value="member">Member</option>
                <option value="admin">Admin</option>
              </select>

              <button
                type="button"
                style={btnSmall}
                // Your own password goes through Change password, which asks for the
                // current one and a 2FA code. This route deliberately asks for
                // neither, which is right for helping a colleague and wrong as a
                // self-service path — the server refuses it too.
                disabled={u.id === currentUser?.id}
                onClick={() => {
                  setResettingId(u.id);
                  setResetPassword('');
                  setResetClears2fa(false);
                }}
              >
                Reset password
              </button>

              <button
                type="button"
                style={btnSmall}
                // You cannot deactivate yourself: it is the one self-inflicted
                // lockout with no in-app way back.
                disabled={u.id === currentUser?.id}
                onClick={() => patchUser(u, { is_active: !u.is_active })}
              >
                {u.is_active ? 'Deactivate' : 'Reactivate'}
              </button>
            </div>
          ))}

          {resettingId !== null && (
            <div style={{ ...rowStyle, alignItems: 'flex-end' }}>
              <div style={{ flex: 1 }}>
                <label htmlFor="reset-password" style={labelStyle}>
                  New password for {users.find(u => u.id === resettingId)?.email}
                </label>
                <input
                  id="reset-password"
                  type="password"
                  autoComplete="new-password"
                  value={resetPassword}
                  onChange={e => setResetPassword(e.target.value)}
                  style={inputStyle}
                />
                <p style={{ color: INK_MUTE, fontSize: 12, margin: 0 }}>
                  There is no email here to send a reset link to, so tell them this
                  password yourself. It ends their existing sessions.
                </p>
                <label style={{
                  display: 'flex', alignItems: 'center', gap: 8, marginTop: 8,
                  fontSize: 12, color: INK_MUTE, cursor: 'pointer',
                }}>
                  <input
                    type="checkbox"
                    checked={resetClears2fa}
                    onChange={e => setResetClears2fa(e.target.checked)}
                  />
                  Also turn off their two-factor authentication
                </label>
                <p style={{ color: INK_MUTE, fontSize: 12, margin: '4px 0 0' }}>
                  Only if they also lost their authenticator and their backup codes —
                  a new password alone still leaves them stuck at the second factor.
                  They should turn it back on once they are in.
                </p>
              </div>
              <button type="button" style={btnPrimary} onClick={() => submitReset(resettingId)}>
                Set password
              </button>
              <button type="button" style={btnSecondary} onClick={() => setResettingId(null)}>
                Cancel
              </button>
            </div>
          )}
        </div>
      )}

      {showInvite ? (
        <div style={{ borderTop: `1px solid ${LINE}`, paddingTop: 12, marginTop: 12 }}>
          <label htmlFor="new-user-email" style={labelStyle}>Email</label>
          <input
            id="new-user-email" type="email" autoComplete="off"
            value={email} onChange={e => setEmail(e.target.value)} style={inputStyle}
          />
          <label htmlFor="new-user-name" style={labelStyle}>Name</label>
          <input
            id="new-user-name" value={name}
            onChange={e => setName(e.target.value)} style={inputStyle}
          />
          <label htmlFor="new-user-password" style={labelStyle}>Temporary password</label>
          <input
            id="new-user-password" type="password" autoComplete="new-password"
            value={password} onChange={e => setPassword(e.target.value)} style={inputStyle}
          />
          <label htmlFor="new-user-role" style={labelStyle}>Role</label>
          <select
            id="new-user-role" value={role}
            onChange={e => setRole(e.target.value as 'admin' | 'member')} style={inputStyle}
          >
            <option value="member">Member — full CRM access</option>
            <option value="admin">Admin — also manages users, keys and integrations</option>
          </select>
          {error && <p style={{ color: CORAL, fontSize: 13, marginTop: 0 }}>{error}</p>}
          <button
            type="button" style={btnPrimary} disabled={saving || !email || !password}
            onClick={createUser}
          >
            {saving ? 'Adding…' : 'Add user'}
          </button>
          <button
            type="button" style={{ ...btnSecondary, marginLeft: 8 }}
            onClick={() => { setShowInvite(false); setError(''); }}
          >
            Cancel
          </button>
        </div>
      ) : (
        <button
          type="button"
          style={{ ...btnPrimary, marginTop: 12 }}
          onClick={() => setShowInvite(true)}
        >
          Add user
        </button>
      )}
    </SettingsCard>
  );
}
