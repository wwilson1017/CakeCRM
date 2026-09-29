import { useState } from 'react';
import { api } from '../../core/api/client';
import { writeMayHaveLanded } from '../usePatchableAssembly';
import { useAuth } from '../../core/auth/AuthContext';
import { OwnerSelect } from './OwnerSelect';
import { labelStyle, inputStyle, CORAL_TEXT, LINE, INK_DIM, mono } from '../../shared/styles';
import { formModalOverlay, formModalContent, formTitle, btnPrimary, btnSecondary } from '../styles';
import type { CrmCompany } from '../../core/types';
import { ArchiveReasonField } from './ArchiveReasonField';
import { CustomFieldInputs } from './CustomFieldInputs';
import { useCustomFieldsForm, putCustomFields } from './useCustomFieldsForm';

interface Props {
  company?: CrmCompany;
  onClose: () => void;
  /** Receives the saved record so a list page can patch its row without a refetch (#77). */
  onSaved: (saved: CrmCompany) => void;
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

export function CompanyForm({ company, onClose, onSaved, onWriteUncertain }: Props) {
  const { currentUser } = useAuth();
  const isEdit = !!company;
  const [name, setName] = useState(company?.name || '');
  const [domain, setDomain] = useState(company?.domain || '');
  const [industry, setIndustry] = useState(company?.industry || '');
  const [phone, setPhone] = useState(company?.phone || '');
  const [address, setAddress] = useState(company?.address || '');
  const [source, setSource] = useState(company?.source || '');
  const [status, setStatus] = useState(company?.status || 'active');
  // #239: moving a live company INTO archived needs a reason, sent with the same PUT.
  const [archiveReason, setArchiveReason] = useState('');
  const archiving = isEdit && status === 'archived' && company?.status !== 'archived';
  // Owner (issue #60). On an EDIT the record's own owner is used verbatim — `null`
  // means unassigned and must survive, or saving an unrelated field would silently
  // claim someone else's unowned record. On a CREATE the picker shows you as the
  // default, but `owner_id` is only SENT if you actually touch it: an untouched
  // create lets the server assign the caller, which is race-free (currentUser can
  // still be resolving right after login) and keeps one rule in one place.
  const [ownerId, setOwnerId] = useState<number | null>(
    company ? (company.owner_id ?? null) : (currentUser?.id ?? null),
  );
  const [ownerTouched, setOwnerTouched] = useState(false);
  const [notes, setNotes] = useState(company?.notes || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const cf = useCustomFieldsForm('company', company?.id);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) { setError('Name is required'); return; }
    if (archiving && !archiveReason.trim()) { setError('A reason is required to archive'); return; }
    setSaving(true); setError('');
    const payload: Record<string, unknown> = { name, domain, industry, phone, address, source, status, notes };
    // Omitted on an untouched create so the server assigns the caller.
    if (isEdit || ownerTouched) payload.owner_id = ownerId;
    if (archiving) payload.archive_reason = archiveReason.trim();
    const body = JSON.stringify(payload);
    try {
      // Both endpoints return the saved row; keep it so the caller can fold it into a
      // client-loaded list instead of re-sweeping the corpus (#77).
      const saved = isEdit
        ? await api<CrmCompany>(`/api/crm/companies/${company.id}`, { method: 'PUT', body })
        : await api<CrmCompany>('/api/crm/companies', { method: 'POST', body });
      const id = saved.id;
      await putCustomFields('company', id, cf.changedForSave(), isEdit ? 'Company saved' : 'Company created');
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
          {isEdit ? 'Edit Company' : 'New Company'}
        </h2>
        {error && <p style={{ color: CORAL_TEXT, fontSize: 12, marginBottom: 12 }}>{error}</p>}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div><label style={labelStyle}>Name *</label><input value={name} onChange={e => setName(e.target.value)} style={inputStyle} /></div>
          <div><label style={labelStyle}>Domain</label><input value={domain} onChange={e => setDomain(e.target.value)} placeholder="example.com" style={inputStyle} /></div>
          <div><label style={labelStyle}>Industry</label><input value={industry} onChange={e => setIndustry(e.target.value)} style={inputStyle} /></div>
          <div><label style={labelStyle}>Phone</label><input type="tel" value={phone} onChange={e => setPhone(e.target.value)} style={inputStyle} /></div>
          <div><label style={labelStyle}>Address</label><input value={address} onChange={e => setAddress(e.target.value)} style={inputStyle} /></div>
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
              {/* A record cannot be CREATED archived — the server refuses it (#239). */}
              {isEdit && <option value="archived">Archived</option>}
            </select>
          </div>
          {archiving && (
            <ArchiveReasonField id="company-archive-reason" value={archiveReason} onChange={setArchiveReason} />
          )}
          <div>
            <OwnerSelect
              value={ownerId}
              onChange={v => { setOwnerId(v); setOwnerTouched(true); }}
              id="company-owner"
            />
          </div>
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
          <button type="submit" disabled={saving} style={{
            ...btnPrimary, flex: 1, opacity: saving ? 0.5 : 1,
          }}>{saving ? 'Saving...' : isEdit ? 'Update' : 'Create'}</button>
        </div>
      </form>
    </div>
  );
}
