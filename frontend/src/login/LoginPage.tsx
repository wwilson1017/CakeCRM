/**
 * CakeCRM — Login page.
 * Password step, then optional TOTP 2FA step (code or backup code).
 */

import { useState, useEffect, useRef, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../core/auth/AuthContext';

export function LoginPage() {
  const { login, verify2fa } = useAuth();
  const navigate = useNavigate();
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  // 2FA state
  const [step, setStep] = useState<'password' | '2fa'>('password');
  const [pendingToken, setPendingToken] = useState('');
  const [code, setCode] = useState('');
  const [useBackupCode, setUseBackupCode] = useState(false);
  const [trustDevice, setTrustDevice] = useState(false);
  const codeInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (step === '2fa') codeInputRef.current?.focus();
  }, [step]);

  async function handlePasswordSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const result = await login(password);
      if ('requires2fa' in result) {
        setPendingToken(result.pendingToken);
        setStep('2fa');
      } else {
        navigate('/');
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setLoading(false);
    }
  }

  async function handle2faSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await verify2fa(pendingToken, code, trustDevice);
      navigate('/');
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Verification failed');
      setCode('');
    } finally {
      setLoading(false);
    }
  }

  const inputClass =
    'w-full box-border border border-ck-line-strong rounded-md px-3.5 py-3 bg-ck-card text-base text-ck-ink placeholder:text-ck-ink-soft';
  const buttonClass =
    'w-full bg-ck-accent text-ck-accent-ink border-none text-sm font-medium px-4 py-3 rounded-md cursor-pointer disabled:opacity-50';

  return (
    <div className="min-h-screen bg-ck-bg flex items-center justify-center px-7">
      <div className="w-full max-w-sm">
        <div className="mb-10 text-center">
          <div className="text-4xl mb-3">🍰</div>
          <h1 className="font-display text-3xl font-normal text-ck-ink m-0">CakeCRM</h1>
          <p className="text-sm text-ck-ink-mute mt-2">
            Your pipeline, a piece of cake.
          </p>
        </div>

        {step === 'password' ? (
          <form onSubmit={handlePasswordSubmit}>
            <label className="block text-xs font-medium uppercase tracking-wider text-ck-ink-soft mb-1.5">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder="Enter your password"
              autoFocus
              className={inputClass}
            />
            {error && <p className="text-ck-red text-sm mt-3 mb-0">{error}</p>}
            <button type="submit" disabled={loading || !password} className={`${buttonClass} mt-5`}>
              {loading ? 'Signing in...' : 'Sign in'}
            </button>
          </form>
        ) : (
          <form onSubmit={handle2faSubmit}>
            <h2 className="font-display text-xl font-normal text-ck-ink mt-0 mb-1">
              Two-factor authentication
            </h2>
            <p className="text-sm text-ck-ink-mute mt-0 mb-5">
              {useBackupCode
                ? 'Enter one of your backup codes.'
                : 'Enter the 6-digit code from your authenticator app.'}
            </p>
            <input
              ref={codeInputRef}
              type="text"
              inputMode={useBackupCode ? 'text' : 'numeric'}
              value={code}
              onChange={e => setCode(e.target.value)}
              placeholder={useBackupCode ? 'XXXX-XXXX' : '000000'}
              maxLength={useBackupCode ? 9 : 6}
              autoComplete="one-time-code"
              className={`${inputClass} text-center text-xl tracking-[0.2em] font-mono`}
            />
            <label className="flex items-center gap-2 mt-4 text-sm text-ck-ink-mute cursor-pointer">
              <input
                type="checkbox"
                checked={trustDevice}
                onChange={e => setTrustDevice(e.target.checked)}
                className="accent-ck-accent"
              />
              Trust this browser for 30 days
            </label>
            {error && <p className="text-ck-red text-sm mt-3 mb-0">{error}</p>}
            <button type="submit" disabled={loading || !code} className={`${buttonClass} mt-5`}>
              {loading ? 'Verifying...' : 'Verify'}
            </button>
            <div className="flex justify-between mt-4">
              <button
                type="button"
                onClick={() => { setStep('password'); setError(''); setCode(''); }}
                className="bg-transparent border-none text-ck-ink-mute text-sm cursor-pointer p-0"
              >
                &larr; Back
              </button>
              <button
                type="button"
                onClick={() => { setUseBackupCode(!useBackupCode); setCode(''); setError(''); }}
                className="bg-transparent border-none text-ck-accent-text text-sm cursor-pointer p-0"
              >
                {useBackupCode ? 'Use authenticator code' : 'Use a backup code'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
