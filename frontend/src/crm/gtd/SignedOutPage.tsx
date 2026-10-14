/**
 * What the no-login todo app shows when this browser has no live session (#293) — the
 * in-app twin of the server's signed-out page (backend/crm/todo_web.py), for a session
 * that ends while the app is open. Never sends anyone to the CRM login.
 */
export function SignedOutPage() {
  return (
    <div className="min-h-screen bg-sand">
      <div className="mx-auto max-w-md px-4 pt-12">
        <h1 className="font-heading text-2xl font-bold text-charcoal">Sign in to your Todos</h1>
        <p className="mt-2 text-sm text-muted">
          This browser isn't signed in. On a computer, open CakeCRM → Settings → Personal →
          Todo app devices → Show a sign-in code. Scan it with this phone and press "Open my list
          on this device".
        </p>
        <p className="mt-2 text-sm text-muted">Once you are signed in, add this page to your home screen.</p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="mt-6 rounded-lg border border-line bg-cream px-4 py-2 text-sm text-charcoal hover:border-brand"
        >
          Try again
        </button>
      </div>
    </div>
  );
}
