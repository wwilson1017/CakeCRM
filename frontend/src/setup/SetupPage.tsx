/**
 * CakeCRM — AI Assistant Setup page (/setup).
 * Page shell in the HomePage idiom, wrapping the provider card list.
 */

import { Link } from 'react-router-dom';
import { ProviderSetup } from './ProviderSetup';

export function SetupPage() {
  return (
    <div className="min-h-screen bg-ck-bg">
      <header className="flex items-center justify-between px-6 py-4 border-b border-ck-line bg-ck-card">
        <Link to="/" className="flex items-center gap-2.5 no-underline">
          <span className="text-2xl">🍰</span>
          <span className="font-display text-xl text-ck-ink">CakeCRM</span>
        </Link>
        <Link
          to="/"
          className="border border-ck-line-strong rounded-md text-sm text-ck-ink-mute px-3.5 py-1.5 no-underline hover:text-ck-ink"
        >
          Back
        </Link>
      </header>

      <main className="max-w-2xl mx-auto px-6 py-16">
        <h1 className="font-display text-3xl font-normal text-ck-ink mt-0 mb-3">
          AI Assistant Setup
        </h1>
        <p className="text-ck-ink-mute leading-relaxed mb-10">
          Connect an AI provider to power the built-in sales assistant. CakeCRM works
          fully without one — AI features simply stay hidden until a provider is connected.
        </p>

        <ProviderSetup />
      </main>
    </div>
  );
}
