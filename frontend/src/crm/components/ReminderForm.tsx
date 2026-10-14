/**
 * Reminder create / edit / remind-again form, and the void-with-reason form (issue #283).
 *
 * Shaped like TodoForm. The date is a plain YYYY-MM-DD string and the time a plain 'HH:MM'
 * (or empty for a morning reminder), sent as typed — never through `new Date()`. The
 * server decides the instant in the install's timezone and refuses one already past, so
 * its 400 message is shown as-is.
 *
 * Remind again copies the source's note and people server-side, so that mode shows them
 * read-only and edits only the date and time.
 */
import { useState } from 'react';
import { api } from '../../core/api/client';
import { useAuth } from '../../core/auth/AuthContext';
import type { CrmReminder, ReminderRepeat } from '../../core/types';
import { labelStyle, inputStyle, CORAL_TEXT, INK_MUTE } from '../../shared/styles';
import { formModalOverlay, formModalContent, formTitle, btnPrimary, btnSecondary, btnDanger } from '../styles';
import { writeMayHaveLanded } from '../usePatchableAssembly';
import { useLocalDay } from '../useLocalDay';
import { useUsers } from '../useUsers';
import { REPEAT_LABELS, changedFields } from '../remindersCollection';

const MAX_NOTE = 2000;
const MAX_REASON = 500;

interface Props {
  /** Edit this reminder. */
  reminder?: CrmReminder;
  /** Remind again: a new one-time reminder copying this one's note and people. */
  againFrom?: CrmReminder;
  onClose: () => void;
  onSaved: (saved: CrmReminder) => void;
  onWriteUncertain?: (err: unknown) => void;
}

export function ReminderForm({ reminder, againFrom, onClose, onSaved, onWriteUncertain }: Props) {
  const { currentUser } = useAuth();
  const { today } = useLocalDay();
  const { users, nameFor } = useUsers();
  const source = reminder ?? againFrom;
  const [note, setNote] = useState(reminder?.note ?? '');
  const [dueDate, setDueDate] = useState(reminder?.due_date ?? today);
  const [dueTime, setDueTime] = useState(reminder?.due_time ?? '');
  const [repeat, setRepeat] = useState<ReminderRepeat | ''>(reminder?.repeat ?? '');
  const [people, setPeople] = useState<number[]>(
    reminder?.people ?? (currentUser ? [currentUser.id] : []),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  // Active teammates, plus anyone already picked who has since been deactivated, so an
  // edit never silently drops a person the form cannot show.
  const choices = users.filter(u => u.is_active || people.includes(u.id));

  function toggle(id: number) {
    setPeople(p => (p.includes(id) ? p.filter(x => x !== id) : [...p, id]));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!againFrom && !note.trim()) { setError('A reminder needs a note'); return; }
    if (!againFrom && people.length === 0) { setError('Pick at least one person to notify'); return; }
    if (!dueDate) { setError('Pick a date'); return; }
    setSaving(true); setError('');
    const when = { due_date: dueDate, due_time: dueTime || null };
    try {
      let saved: CrmReminder;
      if (againFrom) {
        saved = await api<CrmReminder>(`/api/crm/reminders/${againFrom.id}/remind-again`, {
          method: 'POST', body: JSON.stringify(when),
        });
      } else {
        const body = { note, people, repeat: repeat || null, ...when };
        saved = reminder
          ? await api<CrmReminder>(`/api/crm/reminders/${reminder.id}`, {
            method: 'PUT', body: JSON.stringify(changedFields(reminder, body)),
          })
          : await api<CrmReminder>('/api/crm/reminders', { method: 'POST', body: JSON.stringify(body) });
      }
      onSaved(saved);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to save');
      if (writeMayHaveLanded(err)) onWriteUncertain?.(err);
    }
    setSaving(false);
  }

  const title = againFrom ? 'Remind Again' : reminder ? 'Edit Reminder' : 'New Reminder';
  return (
    <div style={formModalOverlay} onClick={onClose}>
      <form onClick={e => e.stopPropagation()} onSubmit={handleSubmit} style={formModalContent()}>
        <h2 style={formTitle}>{title}</h2>
        {error && <p role="alert" style={{ color: CORAL_TEXT, fontSize: 12, marginBottom: 12 }}>{error}</p>}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {againFrom ? (
            <div style={{ fontSize: 13, color: INK_MUTE, lineHeight: 1.5 }}>
              <div style={{ whiteSpace: 'pre-wrap' }}>{source?.note}</div>
              <div>To: {source?.people.map(nameFor).join(', ')}</div>
            </div>
          ) : (
            <div>
              <label htmlFor="reminder-note" style={labelStyle}>Note *</label>
              <textarea
                id="reminder-note" value={note} maxLength={MAX_NOTE} rows={3}
                onChange={e => setNote(e.target.value)} style={{ ...inputStyle, resize: 'vertical' }}
              />
            </div>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <label htmlFor="reminder-date" style={labelStyle}>Date *</label>
              <input id="reminder-date" type="date" value={dueDate} min={today}
                onChange={e => setDueDate(e.target.value)} style={inputStyle} />
            </div>
            <div>
              <label htmlFor="reminder-time" style={labelStyle}>Time (blank = 9:00)</label>
              <input id="reminder-time" type="time" value={dueTime}
                onChange={e => setDueTime(e.target.value)} style={inputStyle} />
            </div>
          </div>
          {!againFrom && (
            <>
              <div>
                <label htmlFor="reminder-repeat" style={labelStyle}>Repeat</label>
                <select id="reminder-repeat" value={repeat}
                  onChange={e => setRepeat(e.target.value as ReminderRepeat | '')} style={inputStyle}>
                  <option value="">Does not repeat</option>
                  {(Object.keys(REPEAT_LABELS) as ReminderRepeat[]).map(r => (
                    <option key={r} value={r}>{REPEAT_LABELS[r]}</option>
                  ))}
                </select>
              </div>
              <fieldset style={{ border: 'none', padding: 0, margin: 0 }}>
                <legend style={labelStyle}>Notify *</legend>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 160, overflowY: 'auto' }}>
                  {choices.map(u => (
                    <label key={u.id} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
                      <input type="checkbox" checked={people.includes(u.id)} onChange={() => toggle(u.id)} />
                      {u.name.trim() || u.email}{u.is_active ? '' : ' (inactive)'}
                    </label>
                  ))}
                </div>
              </fieldset>
            </>
          )}
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 20 }}>
          <button type="button" onClick={onClose} style={{ ...btnSecondary, flex: 1 }}>Cancel</button>
          <button type="submit" disabled={saving} style={{ ...btnPrimary, flex: 1, opacity: saving ? 0.5 : 1 }}>
            {saving ? 'Saving...' : reminder ? 'Save Changes' : 'Set Reminder'}
          </button>
        </div>
      </form>
    </div>
  );
}

/** Void an upcoming reminder. The reason is required: a voided row is kept as history. */
export function VoidReminderForm(
  { reminder, onClose, onSaved, onWriteUncertain }:
  { reminder: CrmReminder; onClose: () => void; onSaved: (saved: CrmReminder) => void;
    onWriteUncertain?: (err: unknown) => void },
) {
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!reason.trim()) { setError('Say why it is no longer needed'); return; }
    setSaving(true); setError('');
    try {
      onSaved(await api<CrmReminder>(`/api/crm/reminders/${reminder.id}/void`, {
        method: 'POST', body: JSON.stringify({ reason }),
      }));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to void');
      if (writeMayHaveLanded(err)) onWriteUncertain?.(err);
    }
    setSaving(false);
  }

  return (
    <div style={formModalOverlay} onClick={onClose}>
      <form onClick={e => e.stopPropagation()} onSubmit={handleSubmit} style={formModalContent()}>
        <h2 style={formTitle}>Void Reminder</h2>
        <p style={{ fontSize: 13, color: INK_MUTE, marginBottom: 12, whiteSpace: 'pre-wrap' }}>{reminder.note}</p>
        {error && <p role="alert" style={{ color: CORAL_TEXT, fontSize: 12, marginBottom: 12 }}>{error}</p>}
        <label htmlFor="reminder-void-reason" style={labelStyle}>Reason *</label>
        <textarea
          id="reminder-void-reason" value={reason} maxLength={MAX_REASON} rows={3} autoFocus
          onChange={e => setReason(e.target.value)} style={{ ...inputStyle, resize: 'vertical' }}
        />
        <div style={{ display: 'flex', gap: 8, marginTop: 20 }}>
          <button type="button" onClick={onClose} style={{ ...btnSecondary, flex: 1 }}>Cancel</button>
          <button type="submit" disabled={saving} style={{ ...btnDanger, flex: 1, opacity: saving ? 0.5 : 1 }}>
            {saving ? 'Voiding...' : 'Void Reminder'}
          </button>
        </div>
      </form>
    </div>
  );
}
