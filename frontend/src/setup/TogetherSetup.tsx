/**
 * CakeCRM — Together AI key entry (with the free-credits explainer).
 * Posts to the normalized /api/providers/together/connect-key endpoint.
 */

import { useState } from 'react';
import { api } from '../core/api/client';

interface Props {
  onConnected: () => void;
}

export function TogetherSetup({ onConnected }: Props) {
  const [key, setKey] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function connect() {
    if (!key.trim()) return;
    setLoading(true);
    setError('');
    try {
      await api('/api/providers/together/connect-key', {
        method: 'POST',
        body: JSON.stringify({ api_key: key.trim() }),
      });
      onConnected();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Invalid API key');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="bg-ck-raised rounded-md px-4 py-3 flex flex-col gap-2">
        <p className="text-sm text-ck-ink-mute m-0">
          Run open-weight AI models in the cloud for a fraction of the cost.
        </p>
        <ol className="text-xs text-ck-ink-mute m-0 pl-4 list-decimal flex flex-col gap-1">
          <li>Create a free account at together.ai ($25 free credits, no credit card)</li>
          <li>Go to Settings &gt; API Keys and create a key</li>
          <li>Paste it below</li>
        </ol>
      </div>

      <input
        type="password"
        value={key}
        onChange={e => setKey(e.target.value)}
        placeholder="together_..."
        onKeyDown={e => { if (e.key === 'Enter') connect(); }}
        className="w-full box-border border border-ck-line-strong rounded-md bg-ck-card px-3 py-2 text-sm text-ck-ink placeholder:text-ck-ink-soft"
      />

      {error && <p className="text-ck-red text-xs m-0">{error}</p>}

      <a
        href="https://api.together.xyz/settings/api-keys"
        target="_blank"
        rel="noopener noreferrer"
        className="text-xs text-ck-accent-text no-underline hover:underline"
      >
        Get your API key at api.together.xyz &rarr;
      </a>

      <button
        onClick={connect}
        disabled={loading || !key.trim()}
        className="w-full bg-ck-accent text-ck-accent-ink border-none rounded-md text-sm font-medium px-4 py-2 cursor-pointer disabled:opacity-50"
      >
        {loading ? 'Validating...' : 'Connect'}
      </button>
    </div>
  );
}
