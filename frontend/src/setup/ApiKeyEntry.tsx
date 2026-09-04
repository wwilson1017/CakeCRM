/**
 * CakeCRM — API key entry for a single key-based provider.
 * Always posts to the normalized /api/providers/{provider}/connect-key endpoint.
 */

import { useState } from 'react';
import { api } from '../core/api/client';

interface Props {
  provider: string;
  onConnected: () => void;
}

const API_KEY_LINKS: Record<string, { url: string; label: string }> = {
  anthropic: { url: 'https://console.anthropic.com/settings/keys', label: 'Get your API key at console.anthropic.com' },
  openai: { url: 'https://platform.openai.com/api-keys', label: 'Get your API key at platform.openai.com' },
  google: { url: 'https://aistudio.google.com/apikey', label: 'Get your API key at aistudio.google.com' },
  together: { url: 'https://api.together.xyz/settings/api-keys', label: 'Get your API key at api.together.xyz' },
};

const PLACEHOLDERS: Record<string, string> = {
  anthropic: 'sk-ant-...',
  openai: 'sk-...',
  google: 'AIza...',
  together: 'together_...',
};

export function ApiKeyEntry({ provider, onConnected }: Props) {
  const [key, setKey] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function connect() {
    if (!key.trim()) return;
    setLoading(true);
    setError('');
    try {
      await api(`/api/providers/${provider}/connect-key`, {
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

  const link = API_KEY_LINKS[provider];

  return (
    <div className="flex flex-col gap-2.5">
      <input
        type="password"
        value={key}
        onChange={e => setKey(e.target.value)}
        placeholder={PLACEHOLDERS[provider] ?? 'API key'}
        onKeyDown={e => { if (e.key === 'Enter') connect(); }}
        className="w-full box-border border border-ck-line-strong rounded-md bg-ck-card px-3 py-2 text-sm text-ck-ink placeholder:text-ck-ink-soft"
      />
      {error && <p className="text-ck-red-text text-xs m-0">{error}</p>}
      {link && (
        <a
          href={link.url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-ck-accent-text no-underline hover:underline"
        >
          {link.label} &rarr;
        </a>
      )}
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
