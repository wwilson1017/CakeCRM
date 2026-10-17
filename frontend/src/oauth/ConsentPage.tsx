/**
 * Connect your own AI (#322) — the OAuth consent page, `/oauth/consent?request=<id>`.
 *
 * ChatGPT or Claude opens this in its own window when someone adds CakeCRM as a
 * connector. The person signs in HERE (password, then the 2FA code if they use one) and
 * then allows or refuses the named app, choosing Read and write or Read only.
 *
 * The sign-in deliberately bypasses `useAuth()`: that would store the token in this
 * tab's session and broadcast it to every other CakeCRM tab. This window's token lives
 * in component state only and dies with the page. `api()` is avoided too, because its
 * 401 handling would navigate away and lose the request id. The backend refuses any
 * consent whose token was not minted in the last ten minutes.
 */

import { useState, type FormEvent } from 'react';
import { leave } from './leave';

interface ConsentInfo {
  client_name: string;
  redirect_host: string;
  loopback: boolean;
  write_requested: boolean;
  email: string;
}

type Step = 'password' | '2fa' | 'consent' | 'expired' | 'sending';

async function call<T>(path: string, init: RequestInit, token?: string): Promise<{ status: number; body: T }> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, { ...init, headers });
  const body = (await res.json().catch(() => ({}))) as T;
  return { status: res.status, body };
}

function detail(body: unknown, fallback: string): string {
  const d = (body as { detail?: unknown })?.detail;
  return typeof d === 'string' ? d : fallback;
}

export function ConsentPage() {
  const requestId = new URLSearchParams(window.location.search).get('request') || '';
  const [step, setStep] = useState<Step>(requestId ? 'password' : 'expired');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [pendingToken, setPendingToken] = useState('');
  const [code, setCode] = useState('');
  const [token, setToken] = useState('');
  const [info, setInfo] = useState<ConsentInfo | null>(null);
  const [readOnly, setReadOnly] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function loadConsent(jwt: string) {
    const { status, body } = await call<ConsentInfo>(`/api/mcp/consent/${encodeURIComponent(requestId)}`,
      { method: 'GET' }, jwt);
    if (status === 404) { setStep('expired'); return; }
    if (status !== 200) { setError(detail(body, 'Could not load this sign-in request.')); return; }
    setToken(jwt);
    setInfo(body);
    setReadOnly(!body.write_requested);
    setStep('consent');
  }

  async function onPassword(e: FormEvent) {
    e.preventDefault();
    setError(''); setBusy(true);
    try {
      const { status, body } = await call<{ access_token?: string; requires_2fa?: boolean; pending_token?: string }>(
        '/api/login', { method: 'POST', body: JSON.stringify({ email, password }) });
      if (status !== 200) { setError(detail(body, 'Sign-in failed')); return; }
      if (body.requires_2fa && body.pending_token) { setPendingToken(body.pending_token); setStep('2fa'); return; }
      if (body.access_token) await loadConsent(body.access_token);
    } finally { setBusy(false); }
  }

  async function on2fa(e: FormEvent) {
    e.preventDefault();
    setError(''); setBusy(true);
    try {
      const { status, body } = await call<{ access_token?: string }>('/api/login/verify-2fa', {
        method: 'POST', body: JSON.stringify({ pending_token: pendingToken, code, trust_device: false }),
      });
      if (status !== 200 || !body.access_token) { setError(detail(body, 'Verification failed')); setCode(''); return; }
      await loadConsent(body.access_token);
    } finally { setBusy(false); }
  }

  async function answer(approve: boolean) {
    setError(''); setBusy(true);
    try {
      const { status, body } = await call<{ redirect?: string }>(
        `/api/mcp/consent/${encodeURIComponent(requestId)}`,
        { method: 'POST', body: JSON.stringify({ approve, read_only: readOnly }) }, token);
      if (status === 404) { setStep('expired'); return; }
      if (status !== 200 || !body.redirect) { setError(detail(body, 'Something went wrong. Try again.')); return; }
      setStep('sending');
      leave.to(body.redirect);
    } finally { setBusy(false); }
  }

  const inputClass =
    'w-full box-border border border-ck-line-strong rounded-md px-3.5 py-3 bg-ck-card text-base text-ck-ink placeholder:text-ck-ink-soft';
  const buttonClass =
    'w-full bg-ck-accent text-ck-accent-ink border-none text-sm font-medium px-4 py-3 rounded-md cursor-pointer disabled:opacity-50';
  const labelClass = 'block text-xs font-medium uppercase tracking-wider text-ck-ink-soft mb-1.5';

  return (
    <div className="min-h-screen bg-ck-bg flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <img src="/logo-mark.svg" alt="" className="h-12 w-12 mx-auto mb-3" />
          <h1 className="font-display text-2xl font-normal text-ck-ink m-0">Connect an AI tool</h1>
        </div>

        {step === 'expired' && (
          <p className="text-sm text-ck-ink-mute text-center">
            This sign-in request has expired. Go back to the app you were connecting and try again.
          </p>
        )}

        {step === 'password' && (
          <form onSubmit={onPassword}>
            <p className="text-sm text-ck-ink-mute mt-0 mb-5">
              Sign in to CakeCRM to decide whether an AI app may work in your CRM as you.
            </p>
            <label htmlFor="consent-email" className={labelClass}>Email</label>
            <input id="consent-email" type="email" autoComplete="username" value={email} autoFocus
              onChange={e => setEmail(e.target.value)} className={inputClass} />
            <label htmlFor="consent-password" className={`${labelClass} mt-4`}>Password</label>
            <input id="consent-password" type="password" autoComplete="current-password" value={password}
              onChange={e => setPassword(e.target.value)} className={inputClass} />
            {error && <p className="text-ck-red-text text-sm mt-3 mb-0">{error}</p>}
            <button type="submit" disabled={busy || !email || !password} className={`${buttonClass} mt-5`}>
              {busy ? 'Signing in...' : 'Sign in'}
            </button>
          </form>
        )}

        {step === '2fa' && (
          <form onSubmit={on2fa}>
            <p className="text-sm text-ck-ink-mute mt-0 mb-5">
              Enter the 6-digit code from your authenticator app, or a backup code.
            </p>
            <input type="text" value={code} onChange={e => setCode(e.target.value)} autoFocus
              maxLength={9} autoComplete="one-time-code" aria-label="Verification code"
              className={`${inputClass} text-center text-xl tracking-[0.2em] font-mono`} />
            {error && <p className="text-ck-red-text text-sm mt-3 mb-0">{error}</p>}
            <button type="submit" disabled={busy || !code} className={`${buttonClass} mt-5`}>
              {busy ? 'Verifying...' : 'Verify'}
            </button>
          </form>
        )}

        {(step === 'consent' || step === 'sending') && info && (
          <div>
            <p className="text-base text-ck-ink mt-0">
              <strong>{info.client_name}</strong> wants to work in CakeCRM as <strong>{info.email}</strong>.
            </p>
            <p className="text-sm text-ck-ink-mute">
              After you choose, you will be sent back to <strong>{info.redirect_host}</strong>.
              {info.loopback && ' That is an app running on this computer.'}
            </p>
            <fieldset className="border border-ck-line rounded-md px-3.5 py-3 my-5">
              <legend className={labelClass}>It may</legend>
              <label className="flex items-start gap-2 text-sm text-ck-ink cursor-pointer">
                <input type="radio" name="access" checked={!readOnly} disabled={!info.write_requested}
                  onChange={() => setReadOnly(false)} className="accent-ck-accent mt-0.5" />
                <span>Read and write: look things up, and add or change records. It asks you before
                  anything that archives, merges or changes many records at once.</span>
              </label>
              <label className="flex items-start gap-2 text-sm text-ck-ink cursor-pointer mt-3">
                <input type="radio" name="access" checked={readOnly} onChange={() => setReadOnly(true)}
                  className="accent-ck-accent mt-0.5" />
                <span>Read only: look things up, never change anything.</span>
              </label>
            </fieldset>
            <p className="text-xs text-ck-ink-mute">
              It never sees Gmail and can never delete a contact. You can disconnect it any time
              in Settings, under Connect an AI tool.
            </p>
            {error && <p className="text-ck-red-text text-sm mt-3 mb-0">{error}</p>}
            <button type="button" disabled={busy || step === 'sending'} onClick={() => answer(true)}
              className={`${buttonClass} mt-4`}>
              {step === 'sending' ? 'Connecting...' : 'Allow'}
            </button>
            <button type="button" disabled={busy || step === 'sending'} onClick={() => answer(false)}
              className="w-full bg-transparent border border-ck-line-strong text-ck-ink text-sm px-4 py-3 rounded-md cursor-pointer mt-3 disabled:opacity-50">
              Don&apos;t allow
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
