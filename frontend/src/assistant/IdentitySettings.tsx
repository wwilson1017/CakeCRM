// CakeCRM — assistant identity panel.
//
// Two rules shape this component:
//
//  • **The name is a brand, not a field (#71).** Baker is fixed. The server ignores a
//    `name` in the PUT body and resolves the name from a constant, so this shows it
//    read-only rather than offering an input that cannot take effect.
//  • **Only an admin may save (#106).** `PUT /api/assistant/identity` is `require_admin`
//    (one assistant per install), while the GET is member-legal. Every seat gets the
//    assistant, so a member opening this drawer must see the personality that governs
//    it — but showing them a Save button that can only 403 is offering a control that
//    cannot work. Members get the same text, read-only.

import { useEffect, useState } from 'react';

import { api } from '../core/api/client';
import { useAuth } from '../core/auth/AuthContext';
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
  const { isAdmin } = useAuth();
  const [name, setName] = useState('');
  // The admin's draft: '' means "use the built-in default", which is why it is not
  // simply the resolved text. `effective` is what the assistant actually runs on and
  // is what a member is shown.
  const [draft, setDraft] = useState('');
  const [effective, setEffective] = useState('');
  const [usingDefault, setUsingDefault] = useState(true);
  const [loading, setLoading] = useState(true);
  // A failed GET must not leave an editable form behind. `draft` would still be '',
  // which Save sends as "revert to the built-in default" — so one click on a panel
  // that never loaded would silently destroy a custom personality. (Before #71 the
  // blank-NAME guard blocked that save by accident; removing the name field removed
  // the accident with it, so the guard is now explicit.)
  const [loadFailed, setLoadFailed] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    api<Identity>('/api/assistant/identity')
      .then((id) => {
        if (!alive) return;
        setName(id.name);
        setEffective(id.personality);
        setDraft(id.using_default ? '' : id.personality);
        setUsingDefault(id.using_default);
      })
      .catch(() => {
        if (alive) setLoadFailed(true);
        toast.error('Could not load assistant settings.');
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      const id = await api<Identity>('/api/assistant/identity', {
        method: 'PUT',
        body: JSON.stringify({ personality: draft }),
      });
      setUsingDefault(id.using_default);
      setEffective(id.personality);
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
        ) : loadFailed ? (
          <div style={{ color: INK_MUTE, fontSize: 14 }}>
            Could not load the assistant&rsquo;s settings. Close this and try again.
          </div>
        ) : (
          <>
            {/* A caption, not a form label: the input this used to describe is gone, and
                a <label> pointing at no labelable control is an orphan to assistive tech. */}
            <div style={labelStyle}>Name</div>
            <div style={{ fontSize: 14, fontWeight: 600, color: INK }}>{name}</div>
            <div style={{ fontSize: 12, color: INK_MUTE, marginTop: 4 }}>
              Your assistant is always called {name}.
            </div>

            <label style={{ ...labelStyle, marginTop: 12 }}>Personality</label>
            {isAdmin ? (
              <>
                <textarea
                  aria-label="Personality"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
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
                    onClick={() => setDraft('')}
                    style={{ padding: '7px 14px', fontSize: 14, background: 'none', color: INK_MUTE, border: `1px solid ${LINE}`, borderRadius: 8, cursor: 'pointer' }}
                  >
                    Reset to default
                  </button>
                </div>
              </>
            ) : (
              <>
                <textarea
                  aria-label="Personality"
                  value={effective}
                  readOnly
                  rows={7}
                  style={{ ...inputStyle, resize: 'vertical', fontFamily: 'inherit', color: INK_MUTE }}
                />
                <div style={{ fontSize: 12, color: INK_MUTE, marginTop: 4 }}>
                  {usingDefault ? 'Using the built-in default personality. ' : ''}
                  Only an admin can change {name}&rsquo;s personality.
                </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
