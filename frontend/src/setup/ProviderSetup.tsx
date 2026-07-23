/**
 * CakeCRM — provider card list (the live Settings-tab pattern).
 * One card per provider: unconfigured cards render their single connect UI;
 * configured cards show model + tier controls, set-active, and disconnect.
 */

import { useState, useEffect } from 'react';
import { api } from '../core/api/client';
import type { ProviderStatus } from './types';
import { ApiKeyEntry } from './ApiKeyEntry';
import { TogetherSetup } from './TogetherSetup';
import { OllamaSetup } from './OllamaSetup';
import { ModelSelector } from './ModelSelector';
import { TierPicker } from './TierPicker';

interface ProviderDef {
  id: string;
  label: string;
}

const PROVIDERS: ProviderDef[] = [
  { id: 'anthropic', label: 'Anthropic (Claude)' },
  { id: 'openai', label: 'OpenAI (GPT)' },
  { id: 'google', label: 'Google (Gemini)' },
  { id: 'together', label: 'Together AI' },
  { id: 'ollama', label: 'Ollama (Local)' },
];

export function ProviderSetup() {
  const [status, setStatus] = useState<ProviderStatus | null>(null);
  const [loading, setLoading] = useState(true);

  function reload() {
    api<ProviderStatus>('/api/providers')
      .then(setStatus)
      .catch(err => console.error('Failed to load providers:', err))
      .finally(() => setLoading(false));
  }

  useEffect(() => { reload(); }, []);

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <div className="w-6 h-6 border-2 border-ck-accent border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  // Ollama is local-only; hide it on Railway (no local host to reach).
  const providers = PROVIDERS.filter(p => p.id !== 'ollama' || !status?.is_railway);

  function renderConnectUI(p: ProviderDef) {
    if (p.id === 'together') return <TogetherSetup onConnected={reload} />;
    if (p.id === 'ollama') return <OllamaSetup onConnected={reload} />;
    return <ApiKeyEntry provider={p.id} onConnected={reload} />;
  }

  return (
    <div className="flex flex-col gap-3">
      {providers.map(p => {
        const profile = status?.profiles?.[p.id];
        const isConnected = profile?.configured ?? false;
        const isActive = status?.active_provider === p.id;
        const currentModel = isActive ? (status?.active_model ?? '') : '';

        return (
          <div
            key={p.id}
            className={`bg-ck-card border rounded-lg p-5 ${isActive ? 'border-ck-accent' : 'border-ck-line'}`}
          >
            <div className="flex items-center justify-between mb-4">
              <div>
                <p className="font-display text-lg text-ck-ink m-0">{p.label}</p>
                {isActive && (
                  <span className="text-xs uppercase tracking-wider text-ck-accent">Active</span>
                )}
              </div>

              {isConnected && (
                <div className="flex items-center gap-2">
                  {profile?.key_preview && (
                    <span className="text-xs text-ck-ink-soft font-mono">{profile.key_preview}</span>
                  )}
                  <span className="w-1.5 h-1.5 rounded-full bg-ck-green" />
                  <span className="text-xs uppercase tracking-wider text-ck-green">Connected</span>
                </div>
              )}
            </div>

            {!isConnected ? renderConnectUI(p) : (
              <div className="flex flex-col gap-3">
                <ModelSelector provider={p.id} currentModel={currentModel} onChanged={reload} />
                {p.id !== 'ollama' && <TierPicker provider={p.id} onChanged={reload} />}
                {!isActive && (
                  <button
                    onClick={async () => {
                      // Send an empty model so the backend derives THIS provider's
                      // top-tier default — never another card's active_model.
                      await api('/api/providers/active', {
                        method: 'PUT',
                        body: JSON.stringify({ provider: p.id, model: '' }),
                      });
                      reload();
                    }}
                    className="w-full bg-transparent border border-ck-line-strong rounded-md text-sm text-ck-ink-mute px-4 py-2 cursor-pointer hover:text-ck-ink"
                  >
                    Set as active
                  </button>
                )}
                <button
                  onClick={async () => {
                    await api(`/api/providers/${p.id}/disconnect`, { method: 'POST' });
                    reload();
                  }}
                  className="w-full bg-transparent border border-ck-line-strong rounded-md text-sm text-ck-red px-4 py-2 cursor-pointer hover:opacity-80"
                >
                  Disconnect
                </button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
