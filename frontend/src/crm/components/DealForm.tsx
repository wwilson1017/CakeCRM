import { useState, useEffect, useRef } from 'react';
import { api } from '../../core/api/client';
import { useAuth } from '../../core/auth/AuthContext';
import { OwnerSelect } from './OwnerSelect';
import { labelStyle, inputStyle, CORAL_TEXT, LINE, INK_DIM, mono } from '../../shared/styles';
import { formModalOverlay, formModalContent, formTitle, btnPrimary, btnSecondary } from '../styles';
import { STAGE_ORDER } from '../constants';
import type { CrmDeal, CrmContact, CrmCompany } from '../../core/types';
import { CustomFieldInputs } from './CustomFieldInputs';
import { useCustomFieldsForm, putCustomFields } from './useCustomFieldsForm';
import { RecordCombobox } from './RecordCombobox';
import {
  companyLabelOf, companyNameOf, companySublabelOf, contactLabelOf, contactSublabelOf,
  createCompany, createContact, recordId, searchCompanies, searchContacts,
} from '../dealLinkPickers';

/**
 * CREATE only since issue #75 — editing a deal is inline in `DealDetailBody`, inside the shared
 * detail panel, so this form no longer carries an edit mode. #83's archived stage lock went with
 * it: a deal being created cannot be archived.
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
  // The linked records' display names. Held here rather than looked up from a fetched
  // list, which is what ends the capped-page hazard the two <select>s used to carry: an
  // out-of-page link had no <option> and rendered blank, reading as "none".
  //
  // On a CREATE these start blank and are filled by the picker or, for a deal opened from a
  // contact, by the prefill below. `DealDetailBody`'s inline editor carries the edit-side half of
  // this contract — that a row reaching a picker must join contact_name AND company_name.
  const [contactLabel, setContactLabel] = useState('');
  const [companyLabel, setCompanyLabel] = useState('');
  // Owner (issue #60). The picker shows you as the default, but `owner_id` is only
  // SENT if you actually touch it: an untouched create lets the server assign the
  // caller, which is race-free (currentUser can still be resolving right after login)
  // and keeps one rule in one place.
  const [ownerId, setOwnerId] = useState<number | null>(currentUser?.id ?? null);
  const [ownerTouched, setOwnerTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  // A quick-create is in flight in one of the pickers. Closing a picker deliberately does
  // NOT abandon its create (the record is being written either way), so submitting
  // underneath one would save the deal without a link that is about to exist — and leave the
  // just-created contact or company orphaned. Clicking Save is itself a click OUTSIDE the
  // picker, which is exactly how this feature's own acceptance flow ends when performed
  // quickly, so it is the likely case rather than the exotic one.
  const [contactBusy, setContactBusy] = useState(false);
  const [companyBusy, setCompanyBusy] = useState(false);
  const pickerBusy = contactBusy || companyBusy;
  const [error, setError] = useState('');
  const cf = useCustomFieldsForm('deal');

  // Did the user speak for this link themselves? Tracked per FIELD, and deliberately not as
  // one flag for both: the two are independent, so a single flag lets touching one field
  // suppress the other's prefill — leaving a contact linked by id with a blank label, which
  // submits a link nothing on screen shows.
  //
  // This is what a value-based `prev ?? …` check cannot express, in either direction. It
  // cannot tell "never set" from "deliberately cleared", so a cleared company comes back the
  // moment an async prefill lands or another contact is picked. And it cannot tell "the user
  // chose this" from "we auto-filled it", so a company inherited from a previous contact
  // would outrank the one belonging to the contact now selected.
  const contactTouched = useRef(false);
  const companyTouched = useRef(false);

  useEffect(() => {
    // Deal opened from a contact: default the company to THAT contact's company so the
    // deal lands in its rollups, and label the picker with the contact's name. Fetched
    // directly by id — the only way to be sure of a record the search may never return.
    if (contactId != null) {
      api<CrmContact>(`/api/crm/contacts/${contactId}`)
        .then(c => {
          // The contact half defers to the contact field alone, so touching the COMPANY
          // picker while this is in flight no longer strands a contact linked by id behind
          // a blank label.
          if (contactTouched.current) return;
          setContactLabel(c.name);
          // The company half additionally requires the source contact to still be in play:
          // it is DERIVED from this contact, so once the user has cleared or replaced them
          // the derivation is void — filling from it would either resurrect a link they
          // removed or overwrite the company `pickContact` just took from their new choice.
          if (!companyTouched.current && c.company_id != null) {
            setSelectedCompany(c.company_id);
            setCompanyLabel(c.company_name || c.company || '');
          }
        })
        .catch(() => {});
    }
  }, [contactId]);

  // Picking a contact fills the company from that contact unless the user has spoken for
  // the company field themselves — deal↔company links are independent, so a deliberate
  // choice is never overwritten (or nulled) just because the contact changed. The guard is
  // `companyTouched`, NOT "is the company empty": emptiness cannot tell a cleared company
  // from an unset one, so a company the user had just removed would silently return.
  //
  // Takes the RECORD, not an id: the picker already holds the contact it just resolved, so
  // the old `contacts.find(...)` lookup into the capped list is gone — and with it the case
  // where an out-of-page contact silently skipped the auto-fill.
  function pickContact(c: CrmContact | null) {
    contactTouched.current = true;
    setSelectedContact(c?.id ?? null);
    setContactLabel(c?.name || '');
    // An auto-derived company FOLLOWS the contact it was derived from — including to
    // nothing. Only filling when the new contact HAS a company would strand the previous
    // contact's company on the deal, silently linking it to an organisation neither the
    // user nor the current contact ever named. Clearing the contact outright is left alone:
    // there is no new contact to derive from, and the company may be all the user has.
    if (c && !companyTouched.current) {
      setSelectedCompany(c.company_id);
      setCompanyLabel(c.company_id != null ? (c.company_name || c.company || '') : '');
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) { setError('Title is required'); return; }
    if (pickerBusy) { setError('Still creating a linked record — one moment.'); return; }
    setSaving(true); setError('');
    try {
      const body: Record<string, unknown> = {
        title, value: parseFloat(value) || 0,
        probability: parseInt(probability) || 0,
        expected_close_date: expectedClose, notes,
      };
      body.stage = stage;
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
        {error && <p style={{ color: CORAL_TEXT, fontSize: 12, marginBottom: 12 }}>{error}</p>}

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
            onBusyChange={setContactBusy}
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
            getMatchText={companyNameOf}
            getSublabel={companySublabelOf}
            onSelect={co => {
              companyTouched.current = true;
              setSelectedCompany(co?.id ?? null);
              // The DECORATED label, so the archived marker survives selection — the closed
              // control would otherwise show a plain name for an archived company.
              setCompanyLabel(co ? companyLabelOf(co) : '');
            }}
            onBusyChange={setCompanyBusy}
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
              <select
                value={stage}
                onChange={e => setStage(e.target.value)}
                style={{ ...inputStyle, textTransform: 'capitalize' }}
              >
                {STAGE_ORDER.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div><label style={labelStyle}>Value ($)</label><input type="number" step="any" min="0" value={value} onChange={e => setValue(e.target.value)} style={inputStyle} /></div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div><label style={labelStyle}>Probability (%)</label><input type="number" min="0" max="100" value={probability} onChange={e => setProbability(e.target.value)} style={inputStyle} /></div>
            <div><label style={labelStyle}>Forecasted close date</label><input type="date" value={expectedClose} onChange={e => setExpectedClose(e.target.value)} style={inputStyle} /></div>
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
          <button type="submit" disabled={saving || pickerBusy} style={{
            ...btnPrimary, flex: 1, opacity: saving || pickerBusy ? 0.5 : 1,
          }}>{saving ? 'Saving...' : 'Create'}</button>
        </div>
      </form>
    </div>
  );
}
