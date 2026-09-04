import { useState, useEffect, useCallback, useRef } from 'react';
import type { CSSProperties } from 'react';
import { api } from '../../core/api/client';
import type { CrmFieldValue, CrmFieldValuesResult } from '../../core/types';
import { mono, INK, INK_MUTE, INK_DIM, LINE, ACCENT, ACCENT_TEXT, ACCENT_INK } from '../../shared/styles';
import { toast } from '../../shared/toast';
import { CustomFieldInputs, type EditableField } from './CustomFieldInputs';

interface Props {
  entityType: 'contact' | 'company' | 'deal';
  entityId: number;
  sectionStyle?: CSSProperties;
}

const isEmpty = (v: CrmFieldValue) => v.value == null || v.value.trim() === '';

/**
 * The wire format a custom field stores, rendered for reading.
 *
 * Typed on the two fields it actually reads rather than on `CrmFieldValue`, so the Reports
 * rollup (#144) can pass its own `CrmRollupField` and both surfaces render a boolean from
 * ONE definition. A second copy drifts the day another type gets a display form.
 */
export function displayValue(f: { field_type: string; value: string | null }): string {
  if (f.value == null || f.value === '') return '—';
  if (f.field_type === 'boolean') return f.value === '1' ? 'Yes' : f.value === '0' ? 'No' : f.value;
  return f.value;
}

/**
 * Custom Fields — the per-entity values widget shown on the contact/company/deal
 * detail surfaces. Self-fetches its definitions+values by {entityType, entityId}
 * (like NotesThread), renders NOTHING when the entity type has no defined fields
 * (zero UI residue), and offers a type-aware edit mode that saves only changed
 * values. Definitions are managed in Settings; this only edits values.
 */
export function CustomFieldsSection({ entityType, entityId, sectionStyle }: Props) {
  const [fields, setFields] = useState<CrmFieldValue[]>([]);
  const [loadError, setLoadError] = useState(false);
  const [editing, setEditing] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [showAll, setShowAll] = useState(false);

  const reqRef = useRef(0);
  const load = useCallback(async () => {
    const reqId = ++reqRef.current;
    try {
      const data = await api<CrmFieldValue[]>(`/api/crm/${entityType}/${entityId}/fields`);
      if (reqId !== reqRef.current) return;
      setFields(data);
      setLoadError(false);
    } catch {
      if (reqId !== reqRef.current) return;
      setLoadError(true);
    }
  }, [entityType, entityId]);

  useEffect(() => { queueMicrotask(load); }, [load]);

  function startEdit() {
    const seed: Record<string, string> = {};
    for (const f of fields) seed[String(f.field_id)] = f.value ?? '';
    setValues(seed);
    setEditing(true);
  }

  async function save() {
    // Send only changed values so a save touches nothing the user didn't edit.
    const changed: Record<string, string> = {};
    for (const f of fields) {
      const key = String(f.field_id);
      const next = values[key] ?? '';
      if (next !== (f.value ?? '')) changed[key] = next;
    }
    if (Object.keys(changed).length === 0) { setEditing(false); return; }
    setSaving(true);
    try {
      const res = await api<CrmFieldValuesResult>(`/api/crm/${entityType}/${entityId}/fields`, {
        method: 'PUT',
        body: JSON.stringify({ values: changed }),
      });
      // The backend collects unknown/invalid ids into `errors` without failing the
      // whole write — surface them so a stale client can't silently partial-save.
      if (res.errors?.length) toast.error(`Some fields didn't save: ${res.errors.join('; ')}`);
      setEditing(false);
      load();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message.replace(/^API error \d+: /, '') : 'Failed to save custom fields.');
    } finally {
      setSaving(false);
    }
  }

  // Nothing defined for this entity type → render zero UI (unless the load errored).
  if (!loadError && fields.length === 0) return null;

  const wrapper = sectionStyle ?? { marginTop: 24, borderTop: `1px solid ${LINE}`, paddingTop: 24 };
  const editable: EditableField[] = fields.map(f => ({
    fieldId: f.field_id, name: f.name, fieldType: f.field_type,
    options: f.dropdown_options, required: f.is_required === 1,
  }));
  const emptyCount = fields.filter(isEmpty).length;
  const visible = showAll ? fields : fields.filter(f => !isEmpty(f));

  return (
    <div style={wrapper}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <span style={{ ...mono(10, INK_DIM) }}>Custom Fields</span>
        {!loadError && !editing && (
          <button onClick={startEdit} style={linkBtn}>Edit</button>
        )}
      </div>

      {loadError ? (
        <p style={{ color: INK_DIM, fontSize: 13 }}>Couldn't load custom fields.</p>
      ) : editing ? (
        <>
          <CustomFieldInputs
            fields={editable}
            values={values}
            onChange={(id, v) => setValues(prev => ({ ...prev, [String(id)]: v }))}
          />
          <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
            <button onClick={save} disabled={saving} style={{
              background: ACCENT, color: ACCENT_INK, border: 'none',
              padding: '7px 16px', borderRadius: 4, fontSize: 13, fontWeight: 500,
              cursor: saving ? 'default' : 'pointer', opacity: saving ? 0.5 : 1,
            }}>{saving ? 'Saving…' : 'Save'}</button>
            <button onClick={() => setEditing(false)} disabled={saving} style={{
              background: 'transparent', color: INK_MUTE, border: `1px solid ${LINE}`,
              padding: '7px 16px', borderRadius: 4, fontSize: 13, cursor: 'pointer',
            }}>Cancel</button>
          </div>
        </>
      ) : (
        <>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {visible.map(f => (
              <div key={f.field_id} style={{ display: 'flex', gap: 12, alignItems: 'baseline' }}>
                <span style={{ fontSize: 12, color: INK_MUTE, width: 140, flexShrink: 0 }}>
                  {f.name}{f.is_required === 1 ? ' *' : ''}
                </span>
                <span style={{ fontSize: 13, color: INK }}>{displayValue(f)}</span>
              </div>
            ))}
            {visible.length === 0 && (
              <p style={{ color: INK_DIM, fontSize: 13, margin: 0 }}>No values set.</p>
            )}
          </div>
          {emptyCount > 0 && (
            <button onClick={() => setShowAll(v => !v)} style={{ ...linkBtn, marginTop: 10 }}>
              {showAll ? 'Hide empty fields' : `Show all fields (${emptyCount} empty)`}
            </button>
          )}
        </>
      )}
    </div>
  );
}

const linkBtn = {
  background: 'none', border: 'none', color: ACCENT_TEXT,
  fontSize: 12, cursor: 'pointer', padding: 0,
} as const;
