import { useState, useEffect } from 'react';
import { api } from '../../core/api/client';
import { useAuth } from '../../core/auth/AuthContext';
import { OwnerSelect } from './OwnerSelect';
import { labelStyle, inputStyle, CORAL, LINE, INK_DIM, mono } from '../../shared/styles';
import { formModalOverlay, formModalContent, formTitle, btnPrimary, btnSecondary } from '../styles';
import { STAGE_ORDER } from '../constants';
import { isArchivedDeal } from '../pipelineFilters';
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
  const { currentUser } = useAuth();
  const isEdit = !!deal;
  // A soft-archived deal (issue #83) can be edited, but not re-staged — see the Stage field.
  // Shares the board's predicate rather than re-deriving `archived_at != null`, so there is
  // exactly one definition of "archived" in the app.
  const isArchived = !!deal && isArchivedDeal(deal);
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
  // Owner (issue #60). On an EDIT the record's own owner is used verbatim — `null`
  // means unassigned and must survive, or saving an unrelated field would silently
  // claim someone else's unowned record. On a CREATE the picker shows you as the
  // default, but `owner_id` is only SENT if you actually touch it: an untouched
  // create lets the server assign the caller, which is race-free (currentUser can
  // still be resolving right after login) and keeps one rule in one place.
  const [ownerId, setOwnerId] = useState<number | null>(
    deal ? (deal.owner_id ?? null) : (currentUser?.id ?? null),
  );
  const [ownerTouched, setOwnerTouched] = useState(false);
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

  // Same capped-page hazard the contact fetch above already guards against, now
  // binding for companies too: #35 auto-creates a company per distinct imported
  // name, so a deal's linked company can fall outside the alphabetical first 200
  // and the <select> would render blank — reading as "No company". Append the
  // deal's own company so the control shows the truth.
  const companyOptions = selectedCompany != null && !companies.some(c => c.id === selectedCompany)
    ? [...companies, {
        id: selectedCompany,
        name: deal?.company_name || `Company #${selectedCompany}`,
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
        title, value: parseFloat(value) || 0,
        probability: parseInt(probability) || 0,
        expected_close_date: expectedClose, notes,
      };
      // Send `stage` only when it can be user intent. On an archived deal the select is
      // disabled, so its value is just whatever the row carried when this form opened —
      // and if the deal moved stage elsewhere since (the assistant, another tab) that
      // stale value differs from the server's, which refuses a stage change on an archived
      // deal by rejecting the WHOLE update. Omitting the field leaves it unset, so every
      // other edit still saves. This is the same data-loss the disabled select exists to
      // prevent; locking the control alone did not close it.
      if (!isArchived) body.stage = stage;
      body.contact_id = selectedContact;  // always send (null unlinks the contact)
      body.company_id = selectedCompany;  // always send (null unlinks the company)
      // Omitted on an untouched create so the server assigns the caller; on an
      // edit always sent, where null unassigns.
      if (isEdit || ownerTouched) body.owner_id = ownerId;
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
              {/* Locked on an archived deal (issue #83). The server refuses a stage change
                  on one and rejects the WHOLE update, so leaving this editable would throw
                  away every other field the user had just typed. Every other field stays
                  editable — only the stage is refused. */}
              <select
                value={stage}
                onChange={e => setStage(e.target.value)}
                disabled={isArchived}
                style={{ ...inputStyle, textTransform: 'capitalize', opacity: isArchived ? 0.6 : 1 }}
              >
                {STAGE_ORDER.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
              {isArchived && (
                <p style={{ fontSize: 11, color: INK_DIM, margin: '4px 0 0' }}>
                  Restore the deal to change its stage.
                </p>
              )}
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
