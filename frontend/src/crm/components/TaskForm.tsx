import { useState, useEffect } from 'react';
import { api } from '../../core/api/client';
import { useAuth } from '../../core/auth/AuthContext';
import { OwnerSelect } from './OwnerSelect';
import { labelStyle, inputStyle, CORAL } from '../../shared/styles';
import { formModalOverlay, formModalContent, formTitle, btnPrimary, btnSecondary } from '../styles';
import type { CrmContact, CrmTask } from '../../core/types';

interface Props {
  task?: CrmTask;
  contactId?: number;
  dealId?: number;
  onClose: () => void;
  /** Receives the saved task (joined with contact/deal names) so the Tasks list can
   *  patch its row without a refetch (#77). */
  onSaved: (saved: CrmTask) => void;
}

export function TaskForm({ task, contactId, dealId, onClose, onSaved }: Props) {
  const { currentUser } = useAuth();
  const isEdit = !!task;
  const [title, setTitle] = useState(task?.title || '');
  const [description, setDescription] = useState(task?.description || '');
  const [dueDate, setDueDate] = useState(task?.due_date || '');
  const [priority, setPriority] = useState(task?.priority || 'medium');
  const [selectedContact, setSelectedContact] = useState<number | null>(task?.contact_id ?? contactId ?? null);
  const [contacts, setContacts] = useState<CrmContact[]>([]);
  // Owner (issue #60). On an EDIT the record's own owner is used verbatim — `null`
  // means unassigned and must survive, or saving an unrelated field would silently
  // claim someone else's unowned record. On a CREATE the picker shows you as the
  // default, but `owner_id` is only SENT if you actually touch it: an untouched
  // create lets the server assign the caller, which is race-free (currentUser can
  // still be resolving right after login) and keeps one rule in one place.
  const [ownerId, setOwnerId] = useState<number | null>(
    task ? (task.owner_id ?? null) : (currentUser?.id ?? null),
  );
  const [ownerTouched, setOwnerTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!contactId) {
      api<{ contacts: CrmContact[] }>('/api/crm/contacts?limit=200')
        .then(d => setContacts(d.contacts)).catch(() => {});
    }
  }, [contactId]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) { setError('Title is required'); return; }
    setSaving(true); setError('');
    try {
      const body: Record<string, unknown> = { title, description, due_date: dueDate, priority };
      body.contact_id = selectedContact;  // always send (null unlinks the contact)
      // Omitted on an untouched create so the server assigns the caller; on an
      // edit always sent, where null unassigns.
      if (isEdit || ownerTouched) body.owner_id = ownerId;
      if (dealId) body.deal_id = dealId;

      // Both endpoints return get_task, which since #77 carries contact_name/deal_title —
      // so the list can fold the saved row in rather than re-sweep.
      const saved = isEdit
        ? await api<CrmTask>(`/api/crm/tasks/${task.id}`, { method: 'PUT', body: JSON.stringify(body) })
        : await api<CrmTask>('/api/crm/tasks', { method: 'POST', body: JSON.stringify(body) });
      onSaved(saved);
    } catch (err: unknown) { setError(err instanceof Error ? err.message : 'Failed to save'); }
    setSaving(false);
  }

  return (
    <div style={formModalOverlay} onClick={onClose}>
      <form onClick={e => e.stopPropagation()} onSubmit={handleSubmit} style={formModalContent()}>
        <h2 style={formTitle}>
          {isEdit ? 'Edit Task' : 'New Task'}
        </h2>
        {error && <p style={{ color: CORAL, fontSize: 12, marginBottom: 12 }}>{error}</p>}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div>
            <label style={labelStyle}>What needs to be done? *</label>
            <input value={title} onChange={e => setTitle(e.target.value)} style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>Description</label>
            <textarea value={description} onChange={e => setDescription(e.target.value)} rows={2} style={{ ...inputStyle, resize: 'none' }} />
          </div>
          {!contactId && (
            <div>
              <label style={labelStyle}>Contact</label>
              <select value={selectedContact ?? ''} onChange={e => setSelectedContact(e.target.value ? Number(e.target.value) : null)} style={inputStyle}>
                <option value="">No contact</option>
                {contacts.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </div>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <label style={labelStyle}>Due Date</label>
              <input type="date" value={dueDate} onChange={e => setDueDate(e.target.value)} style={inputStyle} />
            </div>
            <div>
              <label style={labelStyle}>Priority</label>
              <select value={priority} onChange={e => setPriority(e.target.value)} style={inputStyle}>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </select>
            </div>
            <div>
              <OwnerSelect
              value={ownerId}
              onChange={v => { setOwnerId(v); setOwnerTouched(true); }}
              id="task-owner"
            />
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 20 }}>
          <button type="button" onClick={onClose} style={{ ...btnSecondary, flex: 1 }}>Cancel</button>
          <button type="submit" disabled={saving} style={{
            ...btnPrimary, flex: 1, opacity: saving ? 0.5 : 1,
          }}>{saving ? 'Saving...' : isEdit ? 'Save Changes' : 'Create Task'}</button>
        </div>
      </form>
    </div>
  );
}
