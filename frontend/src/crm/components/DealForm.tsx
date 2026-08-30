import { useState, useEffect, useRef } from 'react';
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
import { RecordCombobox } from './RecordCombobox';

interface Props {
  deal?: CrmDeal;
  contactId?: number;
  onClose: () => void;
  onSaved: () => void;
}

// How many rows the pickers request per query. Small on purpose: this is a
// search-as-you-type list a human reads, not a page to browse — the old limit=200 fetch
// existed only because a <select> had to contain every option it could ever show.
const PICKER_LIMIT = 20;

// Module-level so their identity is stable across renders: RecordCombobox lists `search`
// in an effect's dependencies, and an inline arrow would re-fire the query every render.
const searchContacts = (query: string) =>
  api<{ contacts: CrmContact[] }>(
    `/api/crm/contacts?limit=${PICKER_LIMIT}${query ? `&q=${encodeURIComponent(query)}` : ''}`,
  ).then(d => d.contacts);

const searchCompanies = (query: string) =>
  api<{ companies: CrmCompany[] }>(
    `/api/crm/companies?limit=${PICKER_LIMIT}${query ? `&q=${encodeURIComponent(query)}` : ''}`,
  ).then(d => d.companies);

// Quick-create sends the NAME ONLY. Omitting owner_id is what lets the server assign the
// caller (_create_payload distinguishes absent from explicit null), which is the same rule
// the untouched deal create relies on — the client never names an owner it wasn't asked for.
const createContact = (name: string) =>
  api<CrmContact>('/api/crm/contacts', { method: 'POST', body: JSON.stringify({ name }) });

// Not POST /companies: that route INSERTs unconditionally and 400s on a name that already
// exists case/whitespace-insensitively. /resolve is the #35 get-or-create primitive, so
// typing an existing company's name here links to it instead of failing (issue #123).
const createCompany = (name: string) =>
  api<CrmCompany>('/api/crm/companies/resolve', { method: 'POST', body: JSON.stringify({ name }) });

const contactLabelOf = (c: CrmContact) => c.name;
const contactSublabelOf = (c: CrmContact) => c.company_name || c.company || '';
const companyLabelOf = (co: CrmCompany) => (co.status === 'archived' ? `${co.name} (archived)` : co.name);
const companySublabelOf = (co: CrmCompany) => co.domain || '';
const recordId = (r: { id: number }) => r.id;


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
  // The linked records' display names. Held here rather than looked up from a fetched
  // list, which is what ends the capped-page hazard the two <select>s used to carry: an
  // out-of-page link had no <option> and rendered blank, reading as "none". get_deal and
  // get_pipeline both join contact_name and company_name, so every path that opens this
  // form arrives with them.
  const [contactLabel, setContactLabel] = useState(deal?.contact_name || '');
  const [companyLabel, setCompanyLabel] = useState(deal?.company_name || '');
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

  // Set the moment the user touches either picker. The prefill below lands
  // asynchronously, and a `prev ?? …` guard cannot tell "never set" from "just cleared" —
  // so without this, clearing a link while that fetch is in flight silently re-fills it,
  // and the deal saves against a company the user had just unlinked.
  const linksTouched = useRef(false);

  useEffect(() => {
    // Deal opened from a contact (create mode): default the company to THAT
    // contact's company so the deal lands in its rollups, and label the picker with
    // the contact's name. Fetched directly by id — the only way to be sure of a
    // record the search may never return.
    if (!deal && contactId != null) {
      api<CrmContact>(`/api/crm/contacts/${contactId}`)
        .then(c => {
          if (linksTouched.current) return;  // the user got there first; their choice wins
          setContactLabel(c.name);
          if (c.company_id != null) {
            setSelectedCompany(c.company_id);
            setCompanyLabel(c.company_name || c.company || '');
          }
        })
        .catch(() => {});
    }
  }, [deal, contactId]);

  // Picking a contact fills the company from that contact ONLY when no company is
  // set yet — deal↔company links are independent, so we never overwrite (or null)
  // a company the user chose deliberately just because they changed the contact.
  //
  // Takes the RECORD, not an id: the picker already holds the contact it just
  // resolved, so the old `contacts.find(...)` lookup into the capped list is gone —
  // and with it the case where an out-of-page contact silently skipped the auto-fill.
  // Reading `selectedCompany` from the render closure is correct here: this only runs
  // from a user gesture, so the value is the one that gesture was aimed at.
  function pickContact(c: CrmContact | null) {
    linksTouched.current = true;
    setSelectedContact(c?.id ?? null);
    setContactLabel(c?.name || '');
    if (c && c.company_id != null && selectedCompany == null) {
      setSelectedCompany(c.company_id);
      setCompanyLabel(c.company_name || c.company || '');
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
          <RecordCombobox<CrmContact>
            label="Contact"
            id="deal-contact"
            value={selectedContact}
            valueLabel={contactLabel}
            emptyLabel="No contact"
            search={searchContacts}
            create={createContact}
            getId={recordId}
            getLabel={contactLabelOf}
            getSublabel={contactSublabelOf}
            onSelect={pickContact}
          />
          <RecordCombobox<CrmCompany>
            label="Company"
            id="deal-company"
            value={selectedCompany}
            valueLabel={companyLabel}
            emptyLabel="No company"
            search={searchCompanies}
            create={createCompany}
            getId={recordId}
            getLabel={companyLabelOf}
            getSublabel={companySublabelOf}
            onSelect={co => {
              linksTouched.current = true;
              setSelectedCompany(co?.id ?? null);
              setCompanyLabel(co?.name || '');
            }}
          />
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
