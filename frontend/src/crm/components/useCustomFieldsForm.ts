import { useState, useEffect, useCallback } from 'react';
import { api } from '../../core/api/client';
import type { CrmFieldDefinition, CrmFieldValue, CrmFieldValuesResult } from '../../core/types';
import { toast } from '../../shared/toast';
import type { EditableField } from './CustomFieldInputs';

/**
 * PUT custom-field values after an entity has been saved. Wrapped in its own
 * try/catch so a values-save failure never loses the just-saved entity — it toasts
 * and returns. `savedLabel` describes what already succeeded (e.g. "Contact created").
 */
export async function putCustomFields(
  entityType: string, entityId: number, values: Record<string, string>, savedLabel: string,
): Promise<void> {
  if (Object.keys(values).length === 0) return;
  try {
    const res = await api<CrmFieldValuesResult>(`/api/crm/${entityType}/${entityId}/fields`, {
      method: 'PUT', body: JSON.stringify({ values }),
    });
    if (res.errors?.length) toast.error(`Some custom fields didn't save: ${res.errors.join('; ')}`);
  } catch {
    toast.error(`${savedLabel} — custom fields didn't save.`);
  }
}

/**
 * Shared custom-field state for the create/edit entity forms (issue #19). Fetches
 * the entity type's definitions, and (on edit) prefills the current values so the
 * form can render + save custom fields alongside the standard columns. The create
 * flow saves the entity first, then PUTs these values for the returned id — see the
 * consuming form's submit handler.
 */
export function useCustomFieldsForm(entityType: 'contact' | 'company' | 'deal', entityId?: number) {
  const [defs, setDefs] = useState<CrmFieldDefinition[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [original, setOriginal] = useState<Record<string, string>>({});

  useEffect(() => {
    api<CrmFieldDefinition[]>(`/api/crm/fields?entity_type=${entityType}`)
      .then(setDefs).catch(() => {});      // no custom-field UI if the fetch fails
  }, [entityType]);

  useEffect(() => {
    if (entityId == null) return;
    api<CrmFieldValue[]>(`/api/crm/${entityType}/${entityId}/fields`)
      .then(rows => {
        const seed: Record<string, string> = {};
        for (const r of rows) seed[String(r.field_id)] = r.value ?? '';
        setValues(seed);
        setOriginal(seed);
      })
      .catch(() => {});
  }, [entityType, entityId]);

  const editableFields: EditableField[] = defs.map(d => ({
    fieldId: d.id, name: d.name, fieldType: d.field_type,
    options: d.dropdown_options, required: d.is_required === 1,
  }));

  const setValue = useCallback((id: number, v: string) => {
    setValues(prev => ({ ...prev, [String(id)]: v }));
  }, []);

  // Values to PUT after the entity is saved: on create, every non-empty value; on
  // edit, only values that changed from what was fetched (so a save touches nothing
  // the user didn't edit).
  const changedForSave = useCallback((): Record<string, string> => {
    const isCreate = entityId == null;
    const out: Record<string, string> = {};
    for (const d of defs) {
      const k = String(d.id);
      const v = values[k] ?? '';
      if (isCreate ? v !== '' : v !== (original[k] ?? '')) out[k] = v;
    }
    return out;
  }, [defs, values, original, entityId]);

  return { editableFields, values, setValue, changedForSave };
}
