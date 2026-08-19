import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../../core/api/client';
import type { CrmFieldDefinition } from '../../core/types';
import { useIsMobile } from '../../shared/useIsMobile';
import { confirmDialog } from '../../shared/confirm';
import { toast } from '../../shared/toast';
import {
  INK, INK_MUTE, INK_DIM, CORAL, ACCENT, FONT_SANS, mono, labelStyle, inputStyle,
  LINE,
} from '../../shared/styles';
import { cardStyle, sectionHeading, btnPrimary, btnSecondary, filterTab } from '../styles';

type EntityType = 'contact' | 'company' | 'deal';
const ENTITY_TABS: { key: EntityType; label: string }[] = [
  { key: 'contact', label: 'Contacts' },
  { key: 'company', label: 'Companies' },
  { key: 'deal', label: 'Deals' },
];
const FIELD_TYPES = ['text', 'number', 'boolean', 'date', 'select'] as const;

// Mirror of field_service._slugify_key — shown as a read-only hint; the server
// re-derives it too, so the UI never has to be trusted.
const slugify = (name: string) => name.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '');

/**
 * CustomFieldSettings — the definitions manager, a second card on the CRM Settings
 * page. Per-entity tabs list the defined fields (with add + delete); values are set
 * elsewhere (entity forms + detail pages). Ported in behavior from cake_os's
 * FieldSettingsPanel, restyled with CakeCRM tokens.
 */
export function CustomFieldSettings() {
  const isMobile = useIsMobile();
  const [activeType, setActiveType] = useState<EntityType>('contact');
  const [defs, setDefs] = useState<CrmFieldDefinition[]>([]);
  const [loadError, setLoadError] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [name, setName] = useState('');
  const [fieldType, setFieldType] = useState<string>('text');
  const [optionsText, setOptionsText] = useState('');
  const [required, setRequired] = useState(false);
  const [saving, setSaving] = useState(false);

  // Track the live tab so a mutation handler's captured (stale) load — fired after
  // the user switched tabs — can't apply the old type's defs under the new tab.
  const activeTypeRef = useRef(activeType);
  useEffect(() => { activeTypeRef.current = activeType; }, [activeType]);
  const reqRef = useRef(0);
  const load = useCallback(async () => {
    const reqId = ++reqRef.current;
    const forType = activeType;
    try {
      const data = await api<CrmFieldDefinition[]>(`/api/crm/fields?entity_type=${activeType}`);
      if (reqId !== reqRef.current || forType !== activeTypeRef.current) return;
      setDefs(data);
      setLoadError(false);
    } catch {
      if (reqId !== reqRef.current || forType !== activeTypeRef.current) return;
      setLoadError(true);
    }
  }, [activeType]);

  useEffect(() => { queueMicrotask(load); }, [load]);

  function resetForm() {
    setName(''); setFieldType('text'); setOptionsText(''); setRequired(false); setShowAdd(false);
  }

  const derivedKey = slugify(name);
  const canSubmit = !!derivedKey && !saving;

  async function addField() {
    if (!canSubmit) return;
    setSaving(true);
    try {
      await api('/api/crm/fields', {
        method: 'POST',
        body: JSON.stringify({
          entity_type: activeType,
          name: name.trim(),
          field_key: derivedKey,
          field_type: fieldType,
          dropdown_options: fieldType === 'select'
            ? optionsText.split(',').map(o => o.trim()).filter(Boolean)
            : null,
          is_required: required,
        }),
      });
      resetForm();
      load();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message.replace(/^API error \d+: /, '') : 'Failed to add field.');
    } finally {
      setSaving(false);
    }
  }

  async function removeField(f: CrmFieldDefinition) {
    const ok = await confirmDialog({
      title: 'Delete custom field?',
      message: `Delete "${f.name}"? All values stored for this field will be lost.`,
      danger: true,
      confirmLabel: 'Delete',
    });
    if (!ok) return;
    try {
      await api(`/api/crm/fields/${f.id}`, { method: 'DELETE' });
      load();
    } catch {
      toast.error('Failed to delete field.');
    }
  }

  const activeLabel = ENTITY_TABS.find(t => t.key === activeType)!.label;

  return (
    <div style={{ ...cardStyle, padding: isMobile ? 20 : 28, marginTop: 24, maxWidth: 620 }}>
      <div style={sectionHeading()}>Custom Fields</div>
      <p style={{
        fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE, lineHeight: 1.6,
        margin: '0 0 20px', maxWidth: 460,
      }}>
        Define your own fields for contacts, companies, and deals. They appear on the
        create/edit forms and detail pages, and are available to the assistant.
      </p>

      {/* Entity tabs */}
      <div style={{ display: 'flex', marginBottom: 20 }}>
        {ENTITY_TABS.map(t => (
          <button key={t.key} onClick={() => { setActiveType(t.key); resetForm(); }}
            style={filterTab(isMobile, activeType === t.key)}>
            {t.label}
          </button>
        ))}
      </div>

      {loadError ? (
        <p style={{ color: CORAL, fontSize: 13 }}>Couldn't load custom fields.</p>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {defs.length === 0 ? (
            <p style={{ color: INK_DIM, fontSize: 13, margin: 0 }}>
              No custom fields for {activeLabel.toLowerCase()} yet.
            </p>
          ) : defs.map(f => (
            <div key={f.id} style={{
              display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
              gap: 12, paddingBottom: 10, borderBottom: `1px solid ${LINE}`,
            }}>
              <div>
                <span style={{ fontSize: 13, color: INK }}>{f.name}</span>
                <span style={{ ...mono(10, INK_DIM), marginLeft: 8 }}>
                  {f.field_key} · {f.field_type}{f.is_required === 1 ? ' · required' : ''}
                </span>
                {f.field_type === 'select' && f.dropdown_options?.length ? (
                  <div style={{ fontSize: 12, color: INK_MUTE, marginTop: 2 }}>
                    {f.dropdown_options.join(', ')}
                  </div>
                ) : null}
              </div>
              <button onClick={() => removeField(f)} style={{
                background: 'none', border: 'none', color: CORAL, fontSize: 12,
                cursor: 'pointer', padding: 0, flexShrink: 0,
              }}>Delete</button>
            </div>
          ))}
        </div>
      )}

      {/* Add form (inline, revealed on demand) */}
      {showAdd ? (
        <div style={{ marginTop: 18, display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div>
            <label style={labelStyle}>Field name</label>
            <input value={name} onChange={e => setName(e.target.value)} style={inputStyle}
              placeholder="e.g. Account Tier" />
            {name.trim() !== '' && (
              <span style={{ ...mono(10, INK_DIM), display: 'block', marginTop: 4 }}>
                Key: {derivedKey || '(needs letters or numbers)'}
              </span>
            )}
          </div>
          <div>
            <label style={labelStyle}>Type</label>
            <select value={fieldType} onChange={e => setFieldType(e.target.value)} style={inputStyle}>
              {FIELD_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          {fieldType === 'select' && (
            <div>
              <label style={labelStyle}>Options (comma-separated)</label>
              <input value={optionsText} onChange={e => setOptionsText(e.target.value)} style={inputStyle}
                placeholder="Gold, Silver, Bronze" />
            </div>
          )}
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: INK_MUTE, cursor: 'pointer' }}>
            <input type="checkbox" checked={required} onChange={e => setRequired(e.target.checked)}
              style={{ accentColor: ACCENT }} />
            Required (shown with * — advisory, not enforced)
          </label>
          <div style={{ display: 'flex', gap: 8 }}>
            <button onClick={addField} disabled={!canSubmit}
              style={{ ...btnPrimary, opacity: canSubmit ? 1 : 0.5, cursor: canSubmit ? 'pointer' : 'default' }}>
              {saving ? 'Adding…' : 'Add field'}
            </button>
            <button onClick={resetForm} style={btnSecondary}>Cancel</button>
          </div>
        </div>
      ) : (
        <button onClick={() => setShowAdd(true)} style={{
          background: 'none', border: 'none', color: ACCENT, fontSize: 13,
          cursor: 'pointer', padding: 0, marginTop: 16,
        }}>+ Add custom field</button>
      )}
    </div>
  );
}
