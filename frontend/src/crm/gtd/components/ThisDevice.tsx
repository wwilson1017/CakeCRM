import { useState } from 'react';
import { signOut } from '../api';

/**
 * The no-login todo app's own session control (#293): sign THIS device out. Every
 * other device stays signed in; "Sign out everywhere" lives in the CRM's Settings.
 */
export function ThisDevice({ onSignedOut = () => window.location.replace('/todo') }: { onSignedOut?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function run() {
    if (!window.confirm('Sign out of your Todos on this device? Every other device stays signed in.')) return;
    setBusy(true);
    setError('');
    try {
      await signOut();
      onSignedOut();
    } catch {
      setError("Couldn't sign out. Try again.");
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={() => void run()}
        disabled={busy}
        className="rounded-lg border border-line bg-cream px-3 py-1.5 text-sm text-charcoal hover:border-brand disabled:opacity-40"
      >
        {busy ? 'Signing out…' : 'Sign out'}
      </button>
      {error && <span role="alert" className="text-xs text-ck-accent-text">{error}</span>}
    </div>
  );
}
