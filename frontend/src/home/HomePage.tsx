/**
 * CakeCRM — Home page (shell placeholder).
 * Shows backend health while the CRM core lands (issue #3 replaces this
 * with the real Dashboard / Pipeline / Contacts / Tasks navigation).
 */

import { useEffect, useState } from 'react';
import { api } from '../core/api/client';
import { useAuth } from '../core/auth/AuthContext';

interface Health {
  status: string;
  version: string;
  databases: Record<string, string>;
}

export function HomePage() {
  const { logout } = useAuth();
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api<Health>('/api/health')
      .then(setHealth)
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load health'));
  }, []);

  return (
    <div className="min-h-screen bg-ck-bg">
      <header className="flex items-center justify-between px-6 py-4 border-b border-ck-line bg-ck-card">
        <div className="flex items-center gap-2.5">
          <span className="text-2xl">🍰</span>
          <span className="font-display text-xl text-ck-ink">CakeCRM</span>
        </div>
        <button
          onClick={logout}
          className="bg-transparent border border-ck-line-strong rounded-md text-sm text-ck-ink-mute px-3.5 py-1.5 cursor-pointer hover:text-ck-ink"
        >
          Sign out
        </button>
      </header>

      <main className="max-w-2xl mx-auto px-6 py-16">
        <h1 className="font-display text-3xl font-normal text-ck-ink mt-0 mb-3">
          The oven is preheating.
        </h1>
        <p className="text-ck-ink-mute leading-relaxed">
          This is the CakeCRM product shell — login, security, and branding are live.
          The CRM itself (contacts, pipeline, deals, tasks) arrives with the next
          build phases.
        </p>

        <div className="mt-10 bg-ck-card border border-ck-line rounded-lg p-5">
          <h2 className="text-xs font-medium uppercase tracking-wider text-ck-ink-soft mt-0 mb-3">
            Backend status
          </h2>
          {error && <p className="text-ck-red text-sm m-0">{error}</p>}
          {health && (
            <div className="text-sm text-ck-ink">
              <div className="flex items-center gap-2">
                <span
                  className={`inline-block w-2 h-2 rounded-full ${
                    health.status === 'ok' ? 'bg-ck-green' : 'bg-ck-amber'
                  }`}
                />
                {health.status === 'ok' ? 'Healthy' : `Status: ${health.status}`}
                <span className="text-ck-ink-soft">· v{health.version}</span>
              </div>
              <ul className="mt-3 mb-0 pl-0 list-none text-ck-ink-mute">
                {Object.entries(health.databases).map(([db, status]) => (
                  <li key={db} className="py-0.5">
                    <span className="font-mono text-xs">{db}</span>
                    <span className="text-ck-ink-soft"> — {status}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
