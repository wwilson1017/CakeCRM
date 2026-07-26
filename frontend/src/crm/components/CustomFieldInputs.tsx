import { labelStyle, inputStyle } from '../../shared/styles';

// One normalized custom-field row to render an editor for. Both the entity forms
// (from CrmFieldDefinition) and CustomFieldsSection (from CrmFieldValue) map into
// this shape, so the type-aware input rendering lives in exactly one place.
export interface EditableField {
  fieldId: number;
  name: string;
  fieldType: string;                 // text | number | boolean | date | select
  options: string[] | null;
  required: boolean;
}

interface Props {
  fields: EditableField[];
  values: Record<string, string>;    // keyed by String(fieldId); "" means unset/clear
  onChange: (fieldId: number, value: string) => void;
}

/**
 * Renders a stack of type-aware inputs for custom fields, bound to a
 * Record<fieldId, string> value map. Shared by the create/edit entity forms and
 * the detail-page CustomFieldsSection so both surfaces render fields identically.
 * Values are always strings (the wire format): booleans as "1"/"0", empty string
 * to clear.
 */
export function CustomFieldInputs({ fields, values, onChange }: Props) {
  if (fields.length === 0) return null;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {fields.map(f => {
        const key = String(f.fieldId);
        const val = values[key] ?? '';
        return (
          <div key={f.fieldId}>
            <label style={labelStyle}>{f.name}{f.required ? ' *' : ''}</label>
            {renderInput(f, val, v => onChange(f.fieldId, v))}
          </div>
        );
      })}
    </div>
  );
}

function renderInput(f: EditableField, value: string, set: (v: string) => void) {
  const style = { ...inputStyle, fontSize: 13 };
  switch (f.fieldType) {
    case 'boolean':
      return (
        <select value={value} onChange={e => set(e.target.value)} style={style}>
          <option value="">—</option>
          <option value="1">Yes</option>
          <option value="0">No</option>
        </select>
      );
    case 'select':
      return (
        <select value={value} onChange={e => set(e.target.value)} style={style}>
          <option value="">—</option>
          {(f.options ?? []).map(opt => <option key={opt} value={opt}>{opt}</option>)}
        </select>
      );
    case 'date':
      return <input type="date" value={value} onChange={e => set(e.target.value)} style={style} />;
    case 'number':
      // step="any" so a fractional value (the backend accepts finite floats) doesn't
      // fail native <input type="number"> validation and block the whole form submit.
      return <input type="number" step="any" value={value} onChange={e => set(e.target.value)} style={style} />;
    default:
      return <input type="text" value={value} onChange={e => set(e.target.value)} style={style} />;
  }
}
