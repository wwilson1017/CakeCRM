import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { ApiError } from '../../core/api/client';
import { getSession, redeemSignIn } from './api';

/**
 * /todo/signin/:token — sign this browser in to the no-login todo app (#293).
 *
 * Loading this page changes NOTHING: link previews, scanners and prefetchers fetch
 * links, so the code is spent only by the button's POST. Do not move the redemption
 * into an effect. After a sign-in the page REPLACES itself with /todo, so Back never
 * returns to a spent code.
 */
export function SignInPage({ onSignedIn = () => window.location.replace('/todo') }: { onSignedIn?: () => void }) {
  const { token = '' } = useParams<{ token: string }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [alreadySignedIn, setAlreadySignedIn] = useState(false);

  useEffect(() => {
    // Read-only: a 401 here is the normal case for a new device.
    getSession().then(() => setAlreadySignedIn(true), () => {});
  }, []);

  async function signIn() {
    setBusy(true);
    setError('');
    try {
      await redeemSignIn(token);
      onSignedIn();
    } catch (e) {
      setError(signInError(e));
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen bg-sand">
      <div className="mx-auto max-w-md px-4 pt-12">
        <h1 className="font-heading text-2xl font-bold text-charcoal">Sign in to your Todos</h1>
        <p className="mt-2 text-sm text-muted">
          This device stays signed in until you sign it out. Signing it in does not sign
          any other device out.
        </p>
        {alreadySignedIn && (
          <p className="mt-2 text-sm text-muted">This browser is already signed in. Signing in again replaces that session.</p>
        )}
        <button
          type="button"
          onClick={() => void signIn()}
          disabled={busy || !token}
          className="mt-6 rounded-lg bg-brand-dark px-4 py-2 text-sm font-heading text-white hover:bg-brand-deep disabled:opacity-40"
        >
          {busy ? 'Signing in…' : 'Open my list on this device'}
        </button>
        {error && <p role="alert" className="mt-3 text-sm text-ck-accent-text">{error}</p>}
      </div>
    </div>
  );
}

function signInError(e: unknown): string {
  const status = e instanceof ApiError ? e.status : 0;
  if (status === 404) {
    return "This sign-in code isn't valid any more. It works once, for one hour — make a new one in Settings → Personal → Todo app devices.";
  }
  if (status === 403) return "This request didn't come from this site. Open the link in your phone's browser.";
  if (status === 429) return 'Too many tries. Wait a few minutes and try again.';
  return "Couldn't sign in. Check your connection and try again.";
}
