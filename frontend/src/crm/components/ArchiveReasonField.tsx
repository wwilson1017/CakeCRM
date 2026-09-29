/**
 * The required "why archive?" field (#239), shown by the contact and company edit forms only
 * while the status is being moved INTO archived. Inline in the form rather than a second
 * dialog stacked on the form's own modal: the reason rides the same PUT as the status, which
 * the server records as an "Archived — <reason>" note with your name.
 */

import { labelStyle, inputStyle, INK_DIM } from '../../shared/styles';
import { MAX_ARCHIVE_REASON } from '../constants';

export function ArchiveReasonField({ id, value, onChange }: {
  id: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div style={{ gridColumn: '1 / -1' }}>
      <label htmlFor={id} style={labelStyle}>Why archive? *</label>
      <textarea
        id={id}
        required
        value={value}
        maxLength={MAX_ARCHIVE_REASON}
        onChange={e => onChange(e.target.value)}
        rows={2}
        style={{ ...inputStyle, resize: 'none' }}
      />
      <div style={{ fontSize: 12, color: INK_DIM, marginTop: 4 }}>
        Recorded in the notes with your name.
      </div>
    </div>
  );
}
