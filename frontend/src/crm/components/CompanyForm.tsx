import { useState } from 'react';
import { api } from '../../core/api/client';
import { labelStyle, inputStyle, CORAL, LINE, INK_DIM, mono } from '../../shared/styles';
import { formModalOverlay, formModalContent, formTitle, btnPrimary, btnSecondary } from '../styles';
import type { CrmCompany } from '../../core/types';
import { CustomFieldInputs } from './CustomFieldInputs';
import { useCustomFieldsForm, putCustomFields } from './useCustomFieldsForm';

interface Props {
  company?: CrmCompany;
  onClose: () => void;
  onSaved: () => void;
}

export function CompanyForm({ company, onClose, onSaved }: Props) {
  const isEdit = !!company;
  const [name, setName] = useState(company?.name || '');
  const [domain, setDomain] = useState(company?.domain || '');
  const [industry, setIndustry] = useState(company?.industry || '');
  const [phone, setPhone] = useState(company?.phone || '');
  const [address, setAddress] = useState(company?.address || '');
  const [source, setSource] = useState(company?.source || '');
  const [status, setStatus] = useState(company?.status || 'active');
  const [notes, setNotes] = useState(company?.notes || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const cf = useCustomFieldsForm('company', company?.id);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) { setError('Name is required'); return; }
    setSaving(true); setError('');
    const body = JSON.stringify({ name, domain, industry, phone, address, source, status, notes });
    try {
      let id: number;
      if (isEdit) {
        await api(`/api/crm/companies/${company.id}`, { method: 'PUT', body });
        id = company.id;
      } else {
        const created = await api<CrmCompany>('/api/crm/companies', { method: 'POST', body });
        id = created.id;
      }
      await putCustomFields('company', id, cf.changedForSave(), isEdit ? 'Company saved' : 'Company created');
      onSaved();
    } catch (err: unknown) { setError(err instanceof Error ? err.message : 'Failed to save'); }
    setSaving(false);
  }

  return (
    <div style={formModalOverlay} onClick={onClose}>
      <form onClick={e => e.stopPropagation()} onSubmit={handleSubmit} style={formModalContent()}>
        <h2 style={formTitle}>
          {isEdit ? 'Edit Company' : 'New Company'}
        </h2>
        {error && <p style={{ color: CORAL, fontSize: 12, marginBottom: 12 }}>{error}</p>}

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
              <option value="archived">Archived</option>
            </select>
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
