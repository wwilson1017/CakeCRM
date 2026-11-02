import { inputStyle, labelStyle, INK_DIM } from '../../shared/styles';
import type { NewContactDetails } from '../dealLinkPickers';

// `disabled` while the save runs: the handlers write the details captured when Save was pressed,
// so an edit typed during the request would be silently dropped.
export function NewContactFields({ email, phone, onChange, idPrefix, disabled }: NewContactDetails & {
  onChange: (details: NewContactDetails) => void;
  idPrefix: string;
  disabled: boolean;
}) {
  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <label htmlFor={`${idPrefix}-email`} style={labelStyle}>Email</label>
          <input id={`${idPrefix}-email`} type="email" value={email} disabled={disabled}
            onChange={e => onChange({ email: e.target.value, phone })} style={inputStyle} />
        </div>
        <div>
          <label htmlFor={`${idPrefix}-phone`} style={labelStyle}>Phone</label>
          <input id={`${idPrefix}-phone`} type="tel" value={phone} disabled={disabled}
            onChange={e => onChange({ email, phone: e.target.value })} style={inputStyle} />
        </div>
      </div>
      <p style={{ color: INK_DIM, fontSize: 12, margin: '6px 0 0' }}>Optional — saved on the new contact.</p>
    </div>
  );
}
