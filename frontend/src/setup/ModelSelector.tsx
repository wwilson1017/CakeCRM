/**
 * CakeCRM — active-model selector for a connected provider.
 * Lists the provider's (cached) models; choosing one PUTs it as the active
 * provider/model. Renders nothing when the provider has no models.
 */

import { useState, useEffect } from 'react';
import { api } from '../core/api/client';
import type { ModelsResponse } from './types';

interface Props {
  provider: string;
  currentModel: string;
  onChanged: () => void;
}

export function ModelSelector({ provider, currentModel, onChanged }: Props) {
  const [models, setModels] = useState<string[]>([]);
  const [selected, setSelected] = useState(currentModel);
  const [syncedModel, setSyncedModel] = useState(currentModel);
  const [saving, setSaving] = useState(false);

  // Reset the local selection when the active model changes underneath us (e.g.
  // after another card's "Set as active" reload). React's sanctioned "adjust
  // state during render" pattern — no effect, so no cascading-render.
  if (currentModel !== syncedModel) {
    setSyncedModel(currentModel);
    setSelected(currentModel);
  }

  useEffect(() => {
    api<ModelsResponse>(`/api/providers/${provider}/models`)
      .then(data => setModels(data.models))
      .catch(err => console.error('Failed to load models:', err));
  }, [provider]);

  async function save(model: string) {
    setSaving(true);
    try {
      await api('/api/providers/active', {
        method: 'PUT',
        body: JSON.stringify({ provider, model }),
      });
      setSelected(model);
      onChanged();
    } catch (err) {
      console.error('Failed to set model:', err);
    } finally {
      setSaving(false);
    }
  }

  if (!models.length) return null;

  return (
    <div>
      <label className="block text-xs uppercase tracking-wider text-ck-ink-soft mb-1.5">Model</label>
      <select
        value={selected || models[0] || ''}
        onChange={e => save(e.target.value)}
        disabled={saving}
        className="w-full box-border border border-ck-line-strong rounded-md bg-ck-card px-3 py-2 text-sm text-ck-ink disabled:opacity-50"
      >
        {models.map(m => <option key={m} value={m}>{m}</option>)}
      </select>
    </div>
  );
}
