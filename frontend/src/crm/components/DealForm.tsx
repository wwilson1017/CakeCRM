import { useState, useEffect } from 'react';
import { api } from '../../core/api/client';
import { useAuth } from '../../core/auth/AuthContext';
import { OwnerSelect } from './OwnerSelect';
import { labelStyle, inputStyle, CORAL, LINE, INK_DIM, mono } from '../../shared/styles';
import { formModalOverlay, formModalContent, formTitle, btnPrimary, btnSecondary } from '../styles';
import { STAGE_ORDER } from '../constants';
import type { CrmDeal, CrmContact, CrmCompany } from '../../core/types';
import { CustomFieldInputs } from './CustomFieldInputs';
import { useCustomFieldsForm, putCustomFields } from './useCustomFieldsForm';

/**
 * CREATE only since issue #75 — editing a deal is inline in `DealDetailBody`, inside the shared
 * detail panel, so this form no longer has an edit mode to carry.
 */
interface Props {
  contactId?: number;
  onClose: () => void;
  onSaved: () => void;
}


export function DealForm({ contactId, onClose, onSaved }: Props) {
  const { currentUser } = useAuth();
  const [title, setTitle] = useState('');
  const [stage, setStage] = useState('lead');
  const [value, setValue] = useState('');
  const [probability, setProbability] = useState('');
  const [expectedClose, setExpectedClose] = useState('');
  const [notes, setNotes] = useState('');
  const [selectedContact, setSelectedContact] = useState<number | null>(contactId ?? null);
  const [selectedCompany, setSelectedCompany] = useState<number | null>(null);
  const [contacts, setContacts] = useState<CrmContact[]>([]);
  const [companies, setCompanies] = useState<CrmCompany[]>([]);
  // Owner (issue #60). The picker shows you as the default, but `owner_id` is only
  // SENT if you actually touch it: an untouched create lets the server assign the
  // caller, which is race-free (currentUser can still be resolving right after login)
  // and keeps one rule in one place.
  const [ownerId, setOwnerId] = useState<number | null>(currentUser?.id ?? null);
  const [ownerTouched, setOwnerTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const cf = useCustomFieldsForm('deal');

  useEffect(() => {
    api<{ contacts: CrmContact[] }>('/api/crm/contacts?limit=200')
      .then(d => setContacts(d.contacts)).catch(() => {});
    api<{ companies: CrmCompany[] }>('/api/crm/companies?limit=200')
      .then(d => setCompanies(d.companies)).catch(() => {});
    // Deal opened from a contact: default the company to THAT contact's company so
    // the deal lands in its rollups. Fetch the contact directly rather than searching
    // the capped 200-row list — an older linked contact may fall outside that page,
    // which would silently skip the default.
    if (contactId != null) {
      api<CrmContact>(`/api/crm/contacts/${contactId}`)
        .then(c => { if (c.company_id != null) setSelectedCompany(prev => prev ?? c.company_id); })
        .catch(() => {});
    }
  }, [contactId]);

  // Same capped-page hazard the contact fetch above already guards against, now
  // binding for companies too: #35 auto-creates a company per distinct imported
  // name, so the company inherited from a contact can fall outside the alphabetical
  // first 200 and the <select> would render blank — reading as "No company". Append
  // it so the control shows the truth.
  const companyOptions = selectedCompany != null && !companies.some(c => c.id === selectedCompany)
    ? [...companies, {
        id: selectedCompany,
        name: `Company #${selectedCompany}`,
        status: 'active',
      }]
    : companies;

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
      // Omitted on an untouched create so the server assigns the caller.
      if (ownerTouched) body.owner_id = ownerId;
      const created = await api<CrmDeal>('/api/crm/deals', { method: 'POST', body: JSON.stringify(body) });
      await putCustomFields('deal', created.id, cf.changedForSave(), 'Deal created');
      onSaved();
    } catch (err: unknown) { setError(err instanceof Error ? err.message : 'Failed to save'); }
    setSaving(false);
  }

  return (
    <div style={formModalOverlay} onClick={onClose}>
      <form onClick={e => e.stopPropagation()} onSubmit={handleSubmit} style={formModalContent()}>
        <h2 style={formTitle}>New Deal</h2>
        {error && <p style={{ color: CORAL, fontSize: 12, marginBottom: 12 }}>{error}</p>}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div><label style={labelStyle}>Title *</label><input value={title} onChange={e => setTitle(e.target.value)} style={inputStyle} /></div>
          <div>
            <label style={labelStyle}>Contact</label>
            <select value={selectedContact ?? ''} onChange={e => pickContact(e.target.value ? Number(e.target.value) : null)} style={inputStyle}>
              <option value="">No contact</option>
              {contacts.map(c => <option key={c.id} value={c.id}>{c.name}{(c.company_name || c.company) ? ` (${c.company_name || c.company})` : ''}</option>)}
            </select>
          </div>
          <div>
            <label style={labelStyle}>Company</label>
            <select value={selectedCompany ?? ''} onChange={e => setSelectedCompany(e.target.value ? Number(e.target.value) : null)} style={inputStyle}>
              <option value="">No company</option>
              {companyOptions.map(co => <option key={co.id} value={co.id}>{co.name}{co.status === 'archived' ? ' (archived)' : ''}</option>)}
            </select>
          </div>
          <div>
            <OwnerSelect
              value={ownerId}
              onChange={v => { setOwnerId(v); setOwnerTouched(true); }}
              id="deal-owner"
            />
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
          }}>{saving ? 'Saving...' : 'Create'}</button>
        </div>
      </form>
    </div>
  );
}
