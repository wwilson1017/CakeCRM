/**
 * CakeCRM — per-provider tier overrides (top/mid/light).
 * Options come from the (cached) live model list; current values from
 * /api/providers/tiers. Each change optimistically PUTs a single-tier override
 * and rolls back on failure. Renders nothing when the provider has no models.
 */

import { useState, useEffect } from 'react';
import { api } from '../core/api/client';
import type { ModelsResponse, TiersResponse } from './types';

interface Props {
  provider: string;
  onChanged?: () => void;
}

const TIERS: Array<{ key: 'top' | 'mid' | 'light'; label: string }> = [
  { key: 'top', label: 'Top' },
  { key: 'mid', label: 'Mid' },
  { key: 'light', label: 'Light' },
];

export function TierPicker({ provider, onChanged }: Props) {
  const [models, setModels] = useState<string[]>([]);
  const [tiers, setTiers] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api<ModelsResponse>(`/api/providers/${provider}/models`)
      .then(d => setModels(d.models))
      .catch(err => console.error('Failed to load models:', err));
    api<TiersResponse>('/api/providers/tiers')
      .then(d => setTiers(d.tier_models?.[provider] || {}))
      .catch(err => console.error('Failed to load tiers:', err));
  }, [provider]);

  async function save(tier: string, model: string) {
    const previous = tiers[tier] ?? '';
    setSaving(true);
    setTiers(prev => ({ ...prev, [tier]: model })); // optimistic
    try {
      await api('/api/providers/tiers', {
        method: 'PUT',
        body: JSON.stringify({ provider, models: { [tier]: model } }),
      });
      onChanged?.();
    } catch (err) {
      console.error('Failed to set tier:', err);
      setTiers(prev => ({ ...prev, [tier]: previous })); // roll back on failure
    } finally {
      setSaving(false);
    }
  }

  if (!models.length) return null;

  // Keep the current resolved value selectable even if it's not in the live list.
  const optionsFor = (current: string) =>
    !current || models.includes(current) ? models : [current, ...models];

  return (
    <div>
      <label className="block text-xs uppercase tracking-wider text-ck-ink-soft mb-1.5">
        Tiers (top/mid/light — used for background AI work)
      </label>
      <div className="flex gap-2">
        {TIERS.map(t => (
          <div key={t.key} className="flex-1 min-w-0">
            <span className="block text-xs uppercase tracking-wider text-ck-ink-soft mb-1">{t.label}</span>
            <select
              value={tiers[t.key] || ''}
              onChange={e => save(t.key, e.target.value)}
              disabled={saving}
              className="w-full box-border border border-ck-line-strong rounded-md bg-ck-card px-2 py-1.5 text-xs text-ck-ink disabled:opacity-50"
            >
              <option value="">Use inferred default</option>
              {optionsFor(tiers[t.key] || '').map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
        ))}
      </div>
    </div>
  );
}
