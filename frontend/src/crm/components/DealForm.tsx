import { useState, useEffect } from 'react';
import { api } from '../../core/api/client';
import { labelStyle, inputStyle, CORAL, LINE, INK_DIM, mono } from '../../shared/styles';
import { formModalOverlay, formModalContent, formTitle, btnPrimary, btnSecondary } from '../styles';
import { STAGE_ORDER } from '../constants';
import type { CrmDeal, CrmContact, CrmCompany } from '../../core/types';
import { CustomFieldInputs } from './CustomFieldInputs';
import { useCustomFieldsForm, putCustomFields } from './useCustomFieldsForm';

interface Props {
  deal?: CrmDeal;
  contactId?: number;
  onClose: () => void;
  onSaved: () => void;
}


export function DealForm({ deal, contactId, onClose, onSaved }: Props) {
  const isEdit = !!deal;
  const [title, setTitle] = useState(deal?.title || '');
  const [stage, setStage] = useState(deal?.stage || 'lead');
  const [value, setValue] = useState(deal?.value?.toString() || '');
  const [probability, setProbability] = useState(deal?.probability?.toString() || '');
  const [expectedClose, setExpectedClose] = useState(deal?.expected_close_date || '');
  const [notes, setNotes] = useState(deal?.notes || '');
  const [selectedContact, setSelectedContact] = useState<number | null>(deal?.contact_id ?? contactId ?? null);
  const [selectedCompany, setSelectedCompany] = useState<number | null>(deal?.company_id ?? null);
  const [contacts, setContacts] = useState<CrmContact[]>([]);
  const [companies, setCompanies] = useState<CrmCompany[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const cf = useCustomFieldsForm('deal', deal?.id);

  useEffect(() => {
    api<{ contacts: CrmContact[] }>('/api/crm/contacts?limit=200')
      .then(d => setContacts(d.contacts)).catch(() => {});
    api<{ companies: CrmCompany[] }>('/api/crm/companies?limit=200')
      .then(d => setCompanies(d.companies)).catch(() => {});
    // Deal opened from a contact (create mode): default the company to THAT
    // contact's company so the deal lands in its rollups. Fetch the contact
    // directly rather than searching the capped 200-row list — an older linked
    // contact may fall outside that page, which would silently skip the default.
    if (!deal && contactId != null) {
      api<CrmContact>(`/api/crm/contacts/${contactId}`)
        .then(c => { if (c.company_id != null) setSelectedCompany(prev => prev ?? c.company_id); })
        .catch(() => {});
    }
  }, [deal, contactId]);

  // Picking a contact fills the company from that contact ONLY when no company is
  // set yet — deal↔company links are independent, so we never overwrite (or null)
  // a company the user chose deliberately just because they changed the contact.
  function pickContact(id: number | null) {
    setSelectedContact(id);
    if (id != null) {
      const c = contacts.find(x => x.id === id);
      if (c && c.company_id != null) setSelectedCompany(prev => prev ?? c.company_id);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) { setError('Title is required'); return; }
    setSaving(true); setError('');
    try {
      const body: Record<string, unknown> = {
        title, stage, value: parseFloat(value) || 0,
        probability: parseInt(probability) || 0,
        expected_close_date: expectedClose, notes,
      };
      body.contact_id = selectedContact;  // always send (null unlinks the contact)
      body.company_id = selectedCompany;  // always send (null unlinks the company)
      let id: number;
      if (isEdit) {
        await api(`/api/crm/deals/${deal.id}`, { method: 'PUT', body: JSON.stringify(body) });
        id = deal.id;
      } else {
        const created = await api<CrmDeal>('/api/crm/deals', { method: 'POST', body: JSON.stringify(body) });
        id = created.id;
      }
      await putCustomFields('deal', id, cf.changedForSave(), isEdit ? 'Deal saved' : 'Deal created');
      onSaved();
    } catch (err: unknown) { setError(err instanceof Error ? err.message : 'Failed to save'); }
    setSaving(false);
  }

  return (
    <div style={formModalOverlay} onClick={onClose}>
      <form onClick={e => e.stopPropagation()} onSubmit={handleSubmit} style={formModalContent()}>
        <h2 style={formTitle}>
          {isEdit ? 'Edit Deal' : 'New Deal'}
        </h2>
        {error && <p style={{ color: CORAL, fontSize: 12, marginBottom: 12 }}>{error}</p>}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div><label style={labelStyle}>Title *</label><input value={title} onChange={e => setTitle(e.target.value)} style={inputStyle} /></div>
          <div>
            <label style={labelStyle}>Contact</label>
            <select value={selectedContact ?? ''} onChange={e => pickContact(e.target.value ? Number(e.target.value) : null)} style={inputStyle}>
              <option value="">No contact</option>
              {contacts.map(c => <option key={c.id} value={c.id}>{c.name}{c.company ? ` (${c.company})` : ''}</option>)}
            </select>
          </div>
          <div>
            <label style={labelStyle}>Company</label>
            <select value={selectedCompany ?? ''} onChange={e => setSelectedCompany(e.target.value ? Number(e.target.value) : null)} style={inputStyle}>
              <option value="">No company</option>
              {companies.map(co => <option key={co.id} value={co.id}>{co.name}{co.status === 'archived' ? ' (archived)' : ''}</option>)}
            </select>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <label style={labelStyle}>Stage</label>
              <select value={stage} onChange={e => setStage(e.target.value)} style={{ ...inputStyle, textTransform: 'capitalize' }}>
                {STAGE_ORDER.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div><label style={labelStyle}>Value ($)</label><input type="number" step="any" min="0" value={value} onChange={e => setValue(e.target.value)} style={inputStyle} /></div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div><label style={labelStyle}>Probability (%)</label><input type="number" min="0" max="100" value={probability} onChange={e => setProbability(e.target.value)} style={inputStyle} /></div>
            <div><label style={labelStyle}>Expected Close</label><input type="date" value={expectedClose} onChange={e => setExpectedClose(e.target.value)} style={inputStyle} /></div>
          </div>
          <div><label style={labelStyle}>Notes</label><textarea value={notes} onChange={e => setNotes(e.target.value)} rows={2} style={{ ...inputStyle, resize: 'none' }} /></div>
        </div>

        {cf.editableFields.length > 0 && (
          <div style={{ marginTop: 16, borderTop: `1px solid ${LINE}`, paddingTop: 16 }}>
            <span style={{ ...mono(10, INK_DIM), display: 'block', marginBottom: 12 }}>Custom Fields</span>
            <CustomFieldInputs fields={cf.editableFields} values={cf.values} onChange={cf.setValue} />
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, marginTop: 20 }}>
          <button type="button" onClick={onClose} style={{ ...btnSecondary, flex: 1 }}>Cancel</button>
          <button type="submit" disabled={saving} style={{
            ...btnPrimary, flex: 1, opacity: saving ? 0.5 : 1,
          }}>{saving ? 'Saving...' : isEdit ? 'Update' : 'Create'}</button>
        </div>
      </form>
    </div>
  );
}
