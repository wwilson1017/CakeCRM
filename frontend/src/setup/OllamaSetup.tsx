/**
 * CakeCRM — Ollama (local) setup.
 * Three states: reachable+models (pick & connect), reachable+no-models
 * (pull hint), and not-reachable (serve hint + refresh + advanced base_url).
 */

import { useState, useEffect, useCallback } from 'react';
import { api } from '../core/api/client';
import type { OllamaStatusResponse } from './types';

interface Props {
  onConnected: () => void;
}

const RECOMMENDED_MODELS = [
  { name: 'qwen3.5:4b', desc: 'Lightweight (3.4 GB) — works on any computer' },
  { name: 'qwen3.5:9b', desc: 'Balanced (6 GB) — best quality for the size' },
  { name: 'llama3.1:8b', desc: 'Quality (5 GB) — needs 16 GB RAM' },
];

export function OllamaSetup({ onConnected }: Props) {
  const [status, setStatus] = useState<OllamaStatusResponse | null>(null);
  const [checking, setChecking] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState('');
  const [selectedModel, setSelectedModel] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [customUrl, setCustomUrl] = useState('http://localhost:11434');

  // Manual re-detection (Refresh buttons). Sets `checking` true to re-show the
  // spinner — safe here because it runs from a click handler, not an effect.
  const checkStatus = useCallback(async () => {
    setChecking(true);
    try {
      const data = await api<OllamaStatusResponse>('/api/providers/ollama/status');
      setStatus(data);
      if (data.models.length > 0) setSelectedModel(data.models[0]);
    } catch {
      setStatus({ reachable: false, models: [] });
    } finally {
      setChecking(false);
    }
  }, []);

  // Initial detection on mount. `checking` already starts true, so state is only
  // set in the async continuations (never synchronously in the effect body).
  useEffect(() => {
    api<OllamaStatusResponse>('/api/providers/ollama/status')
      .then(data => {
        setStatus(data);
        if (data.models.length > 0) setSelectedModel(data.models[0]);
      })
      .catch(() => setStatus({ reachable: false, models: [] }))
      .finally(() => setChecking(false));
  }, []);

  async function connect() {
    setConnecting(true);
    setError('');
    try {
      await api('/api/providers/ollama/connect', {
        method: 'POST',
        body: JSON.stringify({ base_url: customUrl, model: selectedModel }),
      });
      onConnected();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to connect to Ollama');
    } finally {
      setConnecting(false);
    }
  }

  if (checking) {
    return (
      <div className="flex items-center gap-2 py-4">
        <div className="w-4 h-4 border-2 border-ck-accent border-t-transparent rounded-full animate-spin" />
        <span className="text-sm text-ck-ink-mute">Detecting Ollama...</span>
      </div>
    );
  }

  // Ollama reachable, with models installed.
  if (status?.reachable && status.models.length > 0) {
    return (
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-ck-green" />
          <span className="text-sm text-ck-green">Ollama detected</span>
        </div>

        <div>
          <label className="block text-xs text-ck-ink-mute mb-1.5">Select a model</label>
          <select
            value={selectedModel}
            onChange={e => setSelectedModel(e.target.value)}
            className="w-full box-border border border-ck-line-strong rounded-md bg-ck-card px-3 py-2 text-sm text-ck-ink"
          >
            {status.models.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>

        {error && <p className="text-ck-red text-xs m-0">{error}</p>}

        <button
          onClick={connect}
          disabled={connecting || !selectedModel}
          className="w-full bg-ck-accent text-ck-accent-ink border-none rounded-md text-sm font-medium px-4 py-2 cursor-pointer disabled:opacity-50"
        >
          {connecting ? 'Connecting...' : 'Connect'}
        </button>
      </div>
    );
  }

  // Ollama reachable but no models installed.
  if (status?.reachable && status.models.length === 0) {
    return (
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-ck-amber" />
          <span className="text-sm text-ck-amber">Ollama is running but no models are installed</span>
        </div>

        <p className="text-xs text-ck-ink-mute m-0">
          Pull a model in your terminal, then click "Refresh":
        </p>

        <div className="flex flex-col gap-2">
          {RECOMMENDED_MODELS.map(m => (
            <div key={m.name} className="bg-ck-raised rounded-md px-3 py-2">
              <code className="text-xs text-ck-accent font-mono">ollama pull {m.name}</code>
              <p className="text-xs text-ck-ink-soft mt-0.5 mb-0">{m.desc}</p>
            </div>
          ))}
        </div>

        <button
          onClick={checkStatus}
          className="w-full bg-ck-accent text-ck-accent-ink border-none rounded-md text-sm font-medium px-4 py-2 cursor-pointer"
        >
          Refresh
        </button>
      </div>
    );
  }

  // Ollama not reachable.
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-ck-red" />
        <span className="text-sm text-ck-red">Ollama not detected</span>
      </div>

      <div className="bg-ck-raised rounded-md px-4 py-3 flex flex-col gap-2">
        <p className="text-sm text-ck-ink-mute m-0">Run AI models locally for free:</p>
        <ol className="text-xs text-ck-ink-mute m-0 pl-4 list-decimal flex flex-col gap-1">
          <li>
            Install Ollama from{' '}
            <a href="https://ollama.com" target="_blank" rel="noopener noreferrer" className="text-ck-accent">
              ollama.com
            </a>
          </li>
          <li>Start it with <code className="text-ck-accent font-mono">ollama serve</code>, then <code className="text-ck-accent font-mono">ollama pull qwen3.5:4b</code></li>
          <li>Come back here and click "Refresh"</li>
        </ol>
      </div>

      <button
        onClick={checkStatus}
        className="w-full bg-transparent border border-ck-line-strong rounded-md text-sm text-ck-ink-mute px-4 py-2 cursor-pointer hover:text-ck-ink"
      >
        Refresh
      </button>

      <button
        onClick={() => setShowAdvanced(!showAdvanced)}
        className="w-full bg-transparent border-none text-xs text-ck-ink-soft cursor-pointer hover:text-ck-ink-mute"
      >
        {showAdvanced ? 'Hide' : 'Advanced'}: Custom Ollama URL
      </button>

      {showAdvanced && (
        <div className="flex flex-col gap-2">
          <input
            type="text"
            value={customUrl}
            onChange={e => setCustomUrl(e.target.value)}
            placeholder="http://localhost:11434"
            className="w-full box-border border border-ck-line-strong rounded-md bg-ck-card px-3 py-2 text-sm text-ck-ink placeholder:text-ck-ink-soft"
          />
          {error && <p className="text-ck-red text-xs m-0">{error}</p>}
          <button
            onClick={connect}
            disabled={connecting || !customUrl.trim()}
            className="w-full bg-ck-accent text-ck-accent-ink border-none rounded-md text-sm font-medium px-4 py-2 cursor-pointer disabled:opacity-50"
          >
            {connecting ? 'Connecting...' : 'Connect'}
          </button>
        </div>
      )}
    </div>
  );
}
