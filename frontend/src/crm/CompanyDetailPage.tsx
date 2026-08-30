import { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { api } from '../core/api/client';
import { rowIsGone, writeMayHaveLanded } from './usePatchableAssembly';
import type { CrmCompany } from '../core/types';
import { CompanyForm } from './components/CompanyForm';
import { ActivityTimeline } from './components/ActivityTimeline';
import { CustomFieldsSection } from './components/CustomFieldsSection';
import { NotesThread } from './components/NotesThread';
import { usePublishActiveRecord } from './RecordContext';
import { StatusBadge } from './components/badges';
import { STAGE_COLORS } from './constants';
import { IconArrowLeft } from '../shared/icons';
import { useIsMobile } from '../shared/useIsMobile';
import { confirmDialog } from '../shared/confirm';
import { toast } from '../shared/toast';
import {
  INK, INK_MUTE, INK_DIM, LINE, LINE_STRONG,
  FONT_DISPLAY,
  mono,
  BG_RAISED,
} from '../shared/styles';
import {
  cardStyle, stageCard,
  btnSecondary, btnDanger, btnSmall,
} from './styles';

/** Optional hooks for the host list page (#77) — see ContactDetailPageProps. */
interface CompanyDetailPageProps {
  onChanged?: (company: CrmCompany) => void;
  onDeleted?: (id: number) => void;
  /** A write here whose outcome is unknown — the host re-sweeps, since only the server
   *  can now say what this record looks like, or whether it still exists (#77). */
  onWriteUncertain?: () => void;
}

export function CompanyDetailPage({ onChanged, onDeleted, onWriteUncertain }: CompanyDetailPageProps = {}) {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const [company, setCompany] = useState<CrmCompany | null>(null);
  const [loading, setLoading] = useState(true);
  const [showEdit, setShowEdit] = useState(false);
  // Bumped on modal save so the self-fetching Custom Fields section remounts + refetches.
  const [cfVersion, setCfVersion] = useState(0);

  // Publish this company as the open record for the assistant drawer (issue #14).
  // type+id come from the route param (always current, even mid-load); the label is
  // only used once the loaded company matches the current route (else "company #N"
  // until it loads — avoids showing the previous company's name during a nav fetch).
  usePublishActiveRecord('company', id ? Number(id) : null,
    company && company.id === Number(id) ? company.name : undefined);

  const loadIdRef = useRef(0);
  const load = useCallback(async () => {
    const reqId = ++loadIdRef.current;
    setLoading(true);
    try {
      const data = await api<CrmCompany>(`/api/crm/companies/${id}`);
      if (reqId !== loadIdRef.current) return;
      setCompany(data);
      onChanged?.(data);
    } catch (err) {
      if (reqId !== loadIdRef.current) return;
      setCompany(null);
      // See ContactDetailPage: a 404 from a stale list row is a ghost, not an error.
      if (rowIsGone(err)) onDeleted?.(Number(id));
    }
    if (reqId === loadIdRef.current) setLoading(false);
  }, [id, onChanged, onDeleted]);

  useEffect(() => { queueMicrotask(load); }, [load]);

  async function handleDelete() {
    const ok = await confirmDialog({
      title: 'Delete company',
      message: `This will permanently delete ${company?.name || 'this company'}. Contacts and deals linked to it are kept and unlinked. This cannot be undone.`,
      confirmLabel: 'Delete company',
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/api/crm/companies/${id}`, { method: 'DELETE' });
    } catch (err) {
      toast.error('Failed to delete company.');
      // The DELETE may have committed before the response was lost, in which case the
      // list is still showing a row that no longer exists.
      if (writeMayHaveLanded(err)) onWriteUncertain?.();
      return;
    }
    onDeleted?.(Number(id));
    navigate('/crm/companies');
  }

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: '80px 0' }}>
        <div className="w-8 h-8 border-2 border-ck-accent border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!company) return <p style={{ color: INK_MUTE, padding: 32 }}>Company not found.</p>;

  const subline = [company.industry, company.domain, company.phone, company.address]
    .filter(Boolean).join('  ·  ');

  return (
    <div style={{ padding: isMobile ? '20px 16px' : '32px 44px', maxWidth: 900 }}>
      {/* Back link */}
      <button onClick={() => navigate('/crm/companies')} style={{
        background: 'none', border: 'none', color: INK_DIM,
        fontSize: 13, cursor: 'pointer', marginBottom: 16,
        display: 'flex', alignItems: 'center', gap: 6,
      }}>
        <IconArrowLeft size={14} strokeWidth={1.85} /> Companies
      </button>

      {/* Header */}
      <div style={{ marginBottom: isMobile ? 20 : 32 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, minWidth: 0 }}>
            <h1 style={{
              fontFamily: FONT_DISPLAY,
              fontSize: isMobile ? 24 : 32, fontWeight: 400, letterSpacing: '-0.02em',
              color: INK, margin: 0,
            }}>{company.name}</h1>
            <StatusBadge status={company.status} />
          </div>
          <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
            <button onClick={() => setShowEdit(true)} style={{ ...btnSecondary, ...btnSmall }}>Edit</button>
            <button onClick={handleDelete} style={{ ...btnDanger, ...btnSmall }}>Delete</button>
          </div>
        </div>
        {subline && <p style={{ fontSize: 14, color: INK_MUTE, marginTop: 6 }}>{subline}</p>}
      </div>

      {/* Notes */}
      {company.notes && (
        <div style={{ ...cardStyle, padding: isMobile ? 14 : 16, marginBottom: isMobile ? 20 : 24 }}>
          <p style={{ ...mono(10), marginBottom: 6 }}>Notes</p>
          <p style={{ fontSize: 13, color: INK_MUTE, whiteSpace: 'pre-wrap', lineHeight: 1.5, margin: 0 }}>{company.notes}</p>
        </div>
      )}

      {/* Contacts + Deals */}
      <div style={{ display: isMobile ? 'flex' : 'grid', flexDirection: isMobile ? 'column' : undefined, gridTemplateColumns: isMobile ? undefined : '1fr 1fr', gap: 24 }}>
        {/* Contacts */}
        <div>
          <span style={{ ...mono(10, INK_DIM), display: 'block', marginBottom: 12 }}>Contacts</span>
          <div style={{ borderTop: `1px solid ${LINE}` }}>
            {!company.contacts?.length ? (
              <p style={{ color: INK_DIM, fontSize: 12, padding: '16px 0' }}>No contacts yet.</p>
            ) : (
              company.contacts.map(c => (
                <div key={c.id} onClick={() => navigate(`/crm/contacts/${c.id}`)} style={{
                  padding: '10px 0', borderBottom: `1px solid ${LINE}`, cursor: 'pointer',
                }}>
                  <p style={{ fontSize: 14, color: INK, margin: 0 }}>{c.name}</p>
                  {(c.title || c.email) && (
                    <p style={{ fontSize: 12, color: INK_DIM, marginTop: 2 }}>
                      {[c.title, c.email].filter(Boolean).join(' · ')}
                    </p>
                  )}
                </div>
              ))
            )}
          </div>
        </div>

        {/* Deals */}
        <div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <span style={mono(10, INK_DIM)}>Deals</span>
            {!!company.open_deal_value && (
              <span style={{ ...mono(10, INK_DIM) }}>Open pipeline: ${company.open_deal_value.toLocaleString()}</span>
            )}
          </div>
          <div style={{ borderTop: `1px solid ${LINE}` }}>
            {!company.deals?.length ? (
              <p style={{ color: INK_DIM, fontSize: 12, padding: '16px 0' }}>No deals yet.</p>
            ) : (
              company.deals.map(d => (
                <div key={d.id} style={{
                  ...stageCard(
                    STAGE_COLORS[d.stage]?.bg || BG_RAISED,
                    STAGE_COLORS[d.stage]?.fill || LINE,
                  ),
                  padding: '12px 14px', marginBottom: 6,
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                }}>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <p style={{ fontSize: 14, color: INK, margin: 0 }}>{d.title}</p>
                    <span style={{
                      ...mono(10), textTransform: 'capitalize', marginTop: 2, display: 'inline-block',
                      color: STAGE_COLORS[d.stage]?.text || INK_DIM,
                    }}>{d.stage}{d.contact_name ? ` · ${d.contact_name}` : ''}</span>
                  </div>
                  <span style={{
                    fontFamily: FONT_DISPLAY,
                    fontSize: 16, color: INK, flexShrink: 0, marginLeft: 12,
                  }}>${d.value.toLocaleString()}</span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* Activity history (rolled up across the company's contacts + deals) */}
      <div style={{ marginTop: 24, borderTop: `1px solid ${LINE_STRONG}`, paddingTop: 24 }}>
        <span style={{ ...mono(10, INK_DIM), display: 'block', marginBottom: 12 }}>Activity History</span>
        <ActivityTimeline activities={company.activity || []} onUpdate={load} />
      </div>

      {/* Chatter — editable notes thread (companies joined in issue #22) */}
      <div style={{ marginTop: 24, borderTop: `1px solid ${LINE_STRONG}`, paddingTop: 24 }}>
        <span style={{ ...mono(10, INK_DIM), display: 'block', marginBottom: 12 }}>Chatter</span>
        <NotesThread key={`company-${company.id}`} entityType="company" entityId={company.id} />
      </div>

      {/* Custom fields — renders nothing when no company fields are defined */}
      <CustomFieldsSection
        key={`company-${company.id}-${cfVersion}`}
        entityType="company"
        entityId={company.id}
        sectionStyle={{ marginTop: 24, borderTop: `1px solid ${LINE_STRONG}`, paddingTop: 24 }}
      />

      {showEdit && <CompanyForm company={company} onWriteUncertain={onWriteUncertain} onClose={() => setShowEdit(false)} onSaved={() => { setShowEdit(false); load(); setCfVersion(v => v + 1); }} />}
    </div>
  );
}
