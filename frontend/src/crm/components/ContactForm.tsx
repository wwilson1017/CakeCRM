import { useState } from 'react';
import { api } from '../../core/api/client';
import { writeMayHaveLanded } from '../usePatchableAssembly';
import { useAuth } from '../../core/auth/AuthContext';
import { OwnerSelect } from './OwnerSelect';
import { labelStyle, inputStyle, ACCENT_TEXT, CORAL, LINE, INK_DIM, mono } from '../../shared/styles';
import { formModalOverlay, formModalContent, formTitle, btnPrimary, btnSecondary } from '../styles';
import type { CrmContact, CrmCompany } from '../../core/types';
import { CustomFieldInputs } from './CustomFieldInputs';
import { useCustomFieldsForm, putCustomFields } from './useCustomFieldsForm';
import { RecordCombobox } from './RecordCombobox';

interface Props {
  contact?: CrmContact;
  onClose: () => void;
  /** Receives the saved record so a list page can patch its row without a refetch (#77). */
  onSaved: (saved: CrmContact) => void;
  /**
   * Fired when a save FAILS in a way that may still have committed (#77).
   *
   * A host holding a client-loaded corpus can no longer rely on the next filter change to
   * refetch, so a write whose response was lost would leave the list stale indefinitely.
   * The host decides what to do — in practice, re-sweep. Optional; a 4xx never fires it,
   * because a refusal wrote nothing.
   */
  onWriteUncertain?: (err: unknown) => void;
}

// How many rows the picker requests per query — the same small search-as-you-type page
// `DealForm` uses, not the `limit=200` bulk fetch the old <select> needed to hold every
// option it could ever display.
const PICKER_LIMIT = 20;

// Module-level so their identity is stable across renders: RecordCombobox lists `search`
// in an effect's dependencies, and an inline arrow would re-fire the query every render.
//
// Deliberately duplicated from `DealForm.tsx` rather than extracted into a shared module:
// that file is rewritten by an open PR (#110), and these are six one-line adapters
// configuring a generic component, not logic. The rule they encode lives once, in
// `RecordCombobox`'s `getMatchText` contract. Worth folding into one module when both
// forms are settled.
const searchCompanies = (query: string) =>
  api<{ companies: CrmCompany[] }>(
    `/api/crm/companies?limit=${PICKER_LIMIT}${query ? `&q=${encodeURIComponent(query)}` : ''}`,
  ).then(d => d.companies);

// Not POST /companies: that route INSERTs unconditionally and 400s on a name that already
// exists case/whitespace-insensitively. /resolve is the #35 get-or-create primitive, so
// typing an existing company's name here links to it instead of failing (issues #123/#126).
const createCompany = (name: string) =>
  api<CrmCompany>('/api/crm/companies/resolve', { method: 'POST', body: JSON.stringify({ name }) });

// Display carries the archived marker; MATCHING must not, or typing an archived company's
// real name reports no exact match and the list offers to create the row above it.
const companyLabelOf = (co: CrmCompany) => (co.status === 'archived' ? `${co.name} (archived)` : co.name);
const companyNameOf = (co: CrmCompany) => co.name;
const companySublabelOf = (co: CrmCompany) => co.domain || '';
const recordId = (r: { id: number }) => r.id;

// The inline "Remove" action on the not-linked hint. Styled as text rather than as one of
// `crm/styles`' buttons because it sits INSIDE a sentence — a padded button there would read
// as a second control rather than as part of the explanation.
const removeLegacyStyle: React.CSSProperties = {
  border: 'none', background: 'transparent', color: ACCENT_TEXT, cursor: 'pointer',
  font: 'inherit', padding: 0, textDecoration: 'underline',
};

export function ContactForm({ contact, onClose, onSaved, onWriteUncertain }: Props) {
  const { currentUser } = useAuth();
  const isEdit = !!contact;
  const [name, setName] = useState(contact?.name || '');
  const [email, setEmail] = useState(contact?.email || '');
  const [phone, setPhone] = useState(contact?.phone || '');
  const [title, setTitle] = useState(contact?.title || '');
  const [source, setSource] = useState(contact?.source || '');
  const [status, setStatus] = useState(contact?.status || 'active');
  const [tags, setTags] = useState(contact?.tags || '');
  const [notes, setNotes] = useState(contact?.notes || '');
  const [companyId, setCompanyId] = useState<number | null>(contact?.company_id ?? null);
  // The linked company's DISPLAY label (decorated with "(archived)" when it applies) and
  // its canonical NAME, held separately because they have different jobs: the label is what
  // the closed control shows, the name is what gets written to the legacy free-text column.
  // Writing the decorated label there would put "Acme (archived)" in `contacts.company`.
  //
  // Held here rather than looked up from a fetched page, which is what ends the capped-page
  // hazard the old <select> carried: a linked company outside the first 200 alphabetical
  // rows had no <option>, so the control rendered blank and read as "No company". That is
  // also why the synthetic-option guard this form used to append is gone.
  //
  // Known gap, identical to DealForm's: an already-linked ARCHIVED company shows
  // undecorated until the picker is opened, because the contact payload carries
  // `company_name` but not the company's status. Any new selection does carry the marker.
  const [companyLabel, setCompanyLabel] = useState(
    contact?.company_id != null ? (contact.company_name || contact.company || '') : '',
  );
  const [companyName, setCompanyName] = useState(
    contact?.company_id != null ? (contact.company_name || contact.company || '') : '',
  );
  // Did the user speak for the company field themselves? This is what lets an untouched
  // edit leave BOTH company columns alone — see handleSubmit. A value-based check cannot
  // express it: `companyId == null` cannot tell "never set" from "deliberately cleared".
  const [companyTouched, setCompanyTouched] = useState(false);
  // A quick-create is in flight. Closing the picker deliberately does NOT abandon its
  // create (the company is being written either way), and clicking Save is itself a click
  // OUTSIDE the picker — so submitting underneath one would file the contact without a
  // link that is about to exist, and leave the new company orphaned.
  const [companyBusy, setCompanyBusy] = useState(false);
  // Owner (issue #60). On an EDIT the record's own owner is used verbatim — `null`
  // means unassigned and must survive, or saving an unrelated field would silently
  // claim someone else's unowned record. On a CREATE the picker shows you as the
  // default, but `owner_id` is only SENT if you actually touch it: an untouched
  // create lets the server assign the caller, which is race-free (currentUser can
  // still be resolving right after login) and keeps one rule in one place.
  const [ownerId, setOwnerId] = useState<number | null>(
    contact ? (contact.owner_id ?? null) : (currentUser?.id ?? null),
  );
  const [ownerTouched, setOwnerTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const cf = useCustomFieldsForm('contact', contact?.id);

  // Legacy free text with no company row behind it — a pre-#35 import the backfill
  // migration could not match. `contacts.company` is non-authoritative (issue #35), but it
  // is the only record of that name, so the form SHOWS it rather than rendering the field
  // empty, and PRESERVES it unless the user speaks for the field (see handleSubmit).
  //
  // It stops being shown the moment they do: leaving it as the empty label after a clear
  // would keep advertising a company they had just removed.
  const legacyText = (contact?.company_id == null ? contact?.company : '')?.trim() || '';
  const showLegacy = !companyTouched && legacyText !== '';

  function pickCompany(co: CrmCompany | null) {
    setCompanyTouched(true);
    setCompanyId(co?.id ?? null);
    setCompanyLabel(co ? companyLabelOf(co) : '');
    setCompanyName(co ? co.name : '');
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) { setError('Name is required'); return; }
    if (companyBusy) { setError('Still creating the company — one moment.'); return; }
    setSaving(true); setError('');
    const payload: Record<string, unknown> = { name, email, phone, title, source, status, tags, notes };
    // The link is the source of truth (issue #35); the legacy free-text column is written
    // only so the two can never contradict — link to Beta while the text still says Acme.
    //
    // On an EDIT with the field untouched BOTH keys are OMITTED, which is the whole guard:
    // `PUT /contacts/:id` reads the body with `model_dump(exclude_unset=True)`, so an
    // omitted key is not written at all. Sending `company: ''` instead would destroy the
    // legacy text of a contact whose company was never linked, for someone who only came
    // here to fix a phone number.
    //
    // That guard holds only while every OTHER base field keeps riding along on an edit,
    // which is what stops the omission from emptying `updates` and tripping the route's
    // "No fields to update" 400. A future dirty-fields-only refactor has to handle a
    // custom-fields-only save before it can narrow this payload.
    //
    // On a CREATE there is no prior value to protect, and the create route dumps WITHOUT
    // `exclude_unset` — absent-vs-null is not even expressible there — so both keys always
    // go. `company_id: null` with non-blank text is the #35 ingestion path (resolve or
    // auto-create), which cannot fire here anyway: this form only ever has text when it
    // also has the id it came from.
    if (!isEdit || companyTouched) {
      payload.company_id = companyId;
      payload.company = companyId != null ? companyName : '';
    }
    // Omitted on an untouched create so the server assigns the caller.
    if (isEdit || ownerTouched) payload.owner_id = ownerId;
    const body = JSON.stringify(payload);
    try {
      // Both endpoints return the saved row; keep it so the caller can fold it into a
      // client-loaded list instead of re-sweeping the corpus (#77).
      const saved = isEdit
        ? await api<CrmContact>(`/api/crm/contacts/${contact.id}`, { method: 'PUT', body })
        : await api<CrmContact>('/api/crm/contacts', { method: 'POST', body });
      const id = saved.id;
      // Save custom fields after the contact itself — a values failure toasts but
      // never loses the saved contact.
      await putCustomFields('contact', id, cf.changedForSave(), isEdit ? 'Contact saved' : 'Contact created');
      onSaved(saved);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to save');
      // A 4xx refused the write, so the host's copy is still correct. Anything else may
      // have committed and lost the response — tell the host so it can re-sweep (#77).
      if (writeMayHaveLanded(err)) onWriteUncertain?.(err);
    }
    setSaving(false);
  }

  return (
    <div style={formModalOverlay} onClick={onClose}>
      <form onClick={e => e.stopPropagation()} onSubmit={handleSubmit} style={formModalContent()}>
        <h2 style={formTitle}>
          {isEdit ? 'Edit Contact' : 'New Contact'}
        </h2>
        {error && <p style={{ color: CORAL, fontSize: 12, marginBottom: 12 }}>{error}</p>}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div><label style={labelStyle}>Name *</label><input value={name} onChange={e => setName(e.target.value)} style={inputStyle} /></div>
          <div><label style={labelStyle}>Email</label><input type="email" value={email} onChange={e => setEmail(e.target.value)} style={inputStyle} /></div>
          <div><label style={labelStyle}>Phone</label><input type="tel" value={phone} onChange={e => setPhone(e.target.value)} style={inputStyle} /></div>
          {/* One company field, replacing the free-text input and the capped <select> that
              used to sit beside it (issue #126). Typing a name nothing matches offers to
              create the company, so the freetext escape hatch is not lost — it now
              produces a real, deduplicated company row instead of an orphan string. */}
          <div>
            <RecordCombobox<CrmCompany>
              label="Company"
              id="contact-company"
              value={companyId}
              valueLabel={companyLabel}
              emptyLabel={showLegacy ? legacyText : 'No company'}
              search={searchCompanies}
              create={createCompany}
              getId={recordId}
              getLabel={companyLabelOf}
              getMatchText={companyNameOf}
              getSublabel={companySublabelOf}
              onSelect={pickCompany}
              onBusyChange={setCompanyBusy}
            />
            {showLegacy && (
              <p style={{ fontSize: 11, color: INK_DIM, margin: '4px 0 0' }}>
                Not linked to a company record — pick one or create it to link this contact.{' '}
                {/* The picker's own × is gated on `value != null`, so an unlinked contact has
                    no clear action inside the widget — and `companyTouched` is only set by
                    choosing or clearing. Without this button, removing a wrong legacy name
                    would be impossible without first linking some company to the contact,
                    which is a capability the free-text input used to have.

                    Living OUTSIDE the widget is exactly why it must be inert while a
                    quick-create runs. Every in-widget path that changes the selection bumps
                    `RecordCombobox`'s private intent counter, which is how a create that
                    lands late knows it was superseded; this button cannot reach that counter.
                    Dismissing the popover deliberately does NOT abandon the create, so
                    without the guard: start a create, click away, press Remove, and the
                    create lands afterwards and silently re-links the company that was just
                    removed. Once it has landed the widget's own × is available (the value is
                    non-null by then) and that one DOES supersede. */}
                <button
                  type="button"
                  onClick={() => pickCompany(null)}
                  disabled={companyBusy}
                  style={{ ...removeLegacyStyle, opacity: companyBusy ? 0.5 : 1, cursor: companyBusy ? 'default' : 'pointer' }}
                >
                  Remove
                </button>
              </p>
            )}
          </div>
          <div><label style={labelStyle}>Job Title</label><input value={title} onChange={e => setTitle(e.target.value)} style={inputStyle} /></div>
          <div>
            <label style={labelStyle}>Source</label>
            <select value={source} onChange={e => setSource(e.target.value)} style={inputStyle}>
              <option value="">Select...</option>
              <option value="referral">Referral</option>
              <option value="website">Website</option>
              <option value="cold_call">Cold Call</option>
              <option value="social">Social Media</option>
              <option value="event">Event</option>
              <option value="other">Other</option>
            </select>
          </div>
          <div>
            <label style={labelStyle}>Status</label>
            <select value={status} onChange={e => setStatus(e.target.value)} style={inputStyle}>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
              <option value="archived">Archived</option>
            </select>
          </div>
          <div>
            <OwnerSelect
              value={ownerId}
              onChange={v => { setOwnerId(v); setOwnerTouched(true); }}
              id="contact-owner"
            />
          </div>
          <div><label style={labelStyle}>Tags (comma-separated)</label><input value={tags} onChange={e => setTags(e.target.value)} style={inputStyle} /></div>
          <div><label style={labelStyle}>Notes</label><textarea value={notes} onChange={e => setNotes(e.target.value)} rows={3} style={{ ...inputStyle, resize: 'none' }} /></div>
        </div>

        {cf.editableFields.length > 0 && (
          <div style={{ marginTop: 16, borderTop: `1px solid ${LINE}`, paddingTop: 16 }}>
            <span style={{ ...mono(10, INK_DIM), display: 'block', marginBottom: 12 }}>Custom Fields</span>
            <CustomFieldInputs fields={cf.editableFields} values={cf.values} onChange={cf.setValue} />
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, marginTop: 20 }}>
          <button type="button" onClick={onClose} style={{ ...btnSecondary, flex: 1 }}>Cancel</button>
          <button type="submit" disabled={saving || companyBusy} style={{
            ...btnPrimary, flex: 1, opacity: saving || companyBusy ? 0.5 : 1,
          }}>{saving ? 'Saving...' : isEdit ? 'Update' : 'Create'}</button>
        </div>
      </form>
    </div>
  );
}
