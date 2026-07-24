import { useState, useEffect } from 'react';
import { api } from '../../core/api/client';
import { labelStyle, inputStyle, CORAL } from '../../shared/styles';
import { formModalOverlay, formModalContent, formTitle, btnPrimary, btnSecondary } from '../styles';
import type { CrmContact, CrmCompany } from '../../core/types';

interface Props {
  contact?: CrmContact;
  onClose: () => void;
  onSaved: () => void;
}

export function ContactForm({ contact, onClose, onSaved }: Props) {
  const isEdit = !!contact;
  const [name, setName] = useState(contact?.name || '');
  const [email, setEmail] = useState(contact?.email || '');
  const [phone, setPhone] = useState(contact?.phone || '');
  const [company, setCompany] = useState(contact?.company || '');
  const [title, setTitle] = useState(contact?.title || '');
  const [source, setSource] = useState(contact?.source || '');
  const [status, setStatus] = useState(contact?.status || 'active');
  const [tags, setTags] = useState(contact?.tags || '');
  const [notes, setNotes] = useState(contact?.notes || '');
  const [companyId, setCompanyId] = useState<number | null>(contact?.company_id ?? null);
  const [companies, setCompanies] = useState<CrmCompany[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    api<{ companies: CrmCompany[] }>('/api/crm/companies?limit=200')
      .then(d => setCompanies(d.companies)).catch(() => {});
  }, []);

  // Keep the legacy free-text `company` in sync with the linked company so the two
  // can't contradict (link to Beta while the text still says Acme). Selecting a
  // company overwrites the text; "No company" leaves the text for free-form entry.
  function pickCompany(id: number | null) {
    setCompanyId(id);
    if (id != null) {
      const co = companies.find(c => c.id === id);
      if (co) setCompany(co.name);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) { setError('Name is required'); return; }
    setSaving(true); setError('');
    // company_id always sent (null unlinks — the update endpoint keeps explicit nulls for FKs)
    const body = JSON.stringify({ name, email, phone, company, title, source, status, tags, notes, company_id: companyId });
    try {
      if (isEdit) {
        await api(`/api/crm/contacts/${contact.id}`, { method: 'PUT', body });
      } else {
        await api('/api/crm/contacts', { method: 'POST', body });
      }
      onSaved();
    } catch (err: unknown) { setError(err instanceof Error ? err.message : 'Failed to save'); }
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
          <div><label style={labelStyle}>Company</label><input value={company} onChange={e => setCompany(e.target.value)} style={inputStyle} /></div>
          <div>
            <label style={labelStyle}>Linked Company</label>
            <select value={companyId ?? ''} onChange={e => pickCompany(e.target.value ? Number(e.target.value) : null)} style={inputStyle}>
              <option value="">No company</option>
              {companies.map(co => <option key={co.id} value={co.id}>{co.name}</option>)}
            </select>
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
          <div><label style={labelStyle}>Tags (comma-separated)</label><input value={tags} onChange={e => setTags(e.target.value)} style={inputStyle} /></div>
          <div><label style={labelStyle}>Notes</label><textarea value={notes} onChange={e => setNotes(e.target.value)} rows={3} style={{ ...inputStyle, resize: 'none' }} /></div>
        </div>

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
