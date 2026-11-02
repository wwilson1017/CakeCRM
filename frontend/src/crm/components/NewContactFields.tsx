import { inputStyle, labelStyle, INK_DIM } from '../../shared/styles';
import type { NewContactDetails } from '../dealLinkPickers';

export function NewContactFields({ email, phone, onChange, idPrefix }: NewContactDetails & {
  onChange: (details: NewContactDetails) => void;
  idPrefix: string;
}) {
  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <label htmlFor={`${idPrefix}-email`} style={labelStyle}>Email</label>
          <input id={`${idPrefix}-email`} type="email" value={email}
            onChange={e => onChange({ email: e.target.value, phone })} style={inputStyle} />
        </div>
        <div>
          <label htmlFor={`${idPrefix}-phone`} style={labelStyle}>Phone</label>
          <input id={`${idPrefix}-phone`} type="tel" value={phone}
            onChange={e => onChange({ email, phone: e.target.value })} style={inputStyle} />
        </div>
      </div>
      <p style={{ color: INK_DIM, fontSize: 12, margin: '6px 0 0' }}>Optional — saved on the new contact.</p>
    </div>
  );
}
