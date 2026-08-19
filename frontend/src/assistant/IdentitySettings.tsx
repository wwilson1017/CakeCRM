// CakeCRM — assistant identity editor (name + personality).
// Fulfills the issue's "user-editable in settings" without a training wizard.

import { useEffect, useState } from 'react';

import { api } from '../core/api/client';
import { IconX } from '../shared/icons';
import { toast } from '../shared/toast';
import {
  ACCENT,
  ACCENT_INK,
  BG_CARD,
  INK,
  INK_MUTE,
  inputStyle,
  labelStyle,
  LINE,
  SCRIM,
} from '../shared/styles';

interface Identity {
  name: string;
  personality: string;
  using_default: boolean;
}

export function IdentitySettings({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState('');
  const [personality, setPersonality] = useState('');
  const [usingDefault, setUsingDefault] = useState(true);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    api<Identity>('/api/assistant/identity')
      .then((id) => {
        if (!alive) return;
        setName(id.name);
        setPersonality(id.using_default ? '' : id.personality);
        setUsingDefault(id.using_default);
      })
      .catch(() => toast.error('Could not load assistant settings.'))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  const save = async () => {
    if (!name.trim()) {
      toast.error('Name cannot be blank.');
      return;
    }
    setSaving(true);
    try {
      const id = await api<Identity>('/api/assistant/identity', {
        method: 'PUT',
        body: JSON.stringify({ name: name.trim(), personality }),
      });
      setUsingDefault(id.using_default);
      toast.success('Assistant settings saved.');
      onClose();
    } catch {
      toast.error('Could not save settings.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      style={{
        position: 'absolute', inset: 0, zIndex: 2, background: SCRIM,
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 12,
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{ width: '100%', maxWidth: 380, background: BG_CARD, borderRadius: 12, border: `1px solid ${LINE}`, padding: 16, maxHeight: '100%', overflowY: 'auto' }}
      >
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12 }}>
          <h3 style={{ fontSize: 15, fontWeight: 600, color: INK, margin: 0 }}>Assistant settings</h3>
          <button onClick={onClose} style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', color: INK_MUTE }}>
            <IconX size={18} />
          </button>
        </div>

        {loading ? (
          <div style={{ color: INK_MUTE, fontSize: 14 }}>Loading…</div>
        ) : (
          <>
            <label style={labelStyle}>Name</label>
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} style={inputStyle} />

            <label style={{ ...labelStyle, marginTop: 12 }}>Personality</label>
            <textarea
              value={personality}
              onChange={(e) => setPersonality(e.target.value)}
              maxLength={20000}
              placeholder={usingDefault ? 'Using the built-in default. Type here to customize.' : ''}
              rows={7}
              style={{ ...inputStyle, resize: 'vertical', fontFamily: 'inherit' }}
            />
            <div style={{ fontSize: 12, color: INK_MUTE, marginTop: 4 }}>
              Leave blank to use the built-in default personality.
            </div>

            <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
              <button
                onClick={save}
                disabled={saving}
                style={{
                  padding: '7px 14px', fontSize: 14, fontWeight: 600, background: ACCENT, color: ACCENT_INK,
                  border: 'none', borderRadius: 8, cursor: saving ? 'default' : 'pointer', opacity: saving ? 0.6 : 1,
                }}
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
              <button
                onClick={() => setPersonality('')}
                style={{ padding: '7px 14px', fontSize: 14, background: 'none', color: INK_MUTE, border: `1px solid ${LINE}`, borderRadius: 8, cursor: 'pointer' }}
              >
                Reset to default
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
