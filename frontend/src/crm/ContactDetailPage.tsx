import { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { api } from '../core/api/client';
import { rowIsGone, writeMayHaveLanded } from './usePatchableAssembly';
import type { CrmContact } from '../core/types';
import { ContactForm } from './components/ContactForm';
import { DealForm } from './components/DealForm';
import { TaskForm } from './components/TaskForm';
import { ActivityTimeline } from './components/ActivityTimeline';
import { NotesThread } from './components/NotesThread';
import { CustomFieldsSection } from './components/CustomFieldsSection';
import { usePublishActiveRecord } from './RecordContext';
import { PriorityBadge, ScorePill } from './components/badges';
import { ProvenanceBadge } from './components/ProvenanceBadge';
import { useProvenance } from './useProvenance';
import { STAGE_COLORS } from './constants';
import { IconArrowLeft } from '../shared/icons';
import { useIsMobile } from '../shared/useIsMobile';
import { confirmDialog } from '../shared/confirm';
import { toast } from '../shared/toast';
import {
  INK, INK_MUTE, INK_DIM, LINE, LINE_STRONG, CORAL_FILL, CORAL_TEXT, SAGE_FILL,
  ACCENT, ACCENT_TEXT, ACCENT_INK,
  FONT_DISPLAY, FONT_MONO,
  mono, inputStyle,
  BG_RAISED,
} from '../shared/styles';
import {
  cardStyle, stageCard,
  btnSecondary, btnDanger, btnPrimary, btnSmall,
} from './styles';

/** Optional hooks for the host list page (#77): it keeps a client-loaded corpus and patches
 *  its row from what this page loads, rather than re-sweeping after every edit. */
interface ContactDetailPageProps {
  onChanged?: (contact: CrmContact) => void;
  onDeleted?: (id: number) => void;
  /** A write here whose outcome is unknown — the host re-sweeps, since only the server
   *  can now say what this record looks like, or whether it still exists (#77). */
  onWriteUncertain?: () => void;
}

export function ContactDetailPage({ onChanged, onDeleted, onWriteUncertain }: ContactDetailPageProps = {}) {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const [contact, setContact] = useState<CrmContact | null>(null);
  // Called unconditionally (before the loading/not-found early returns): the hook is a no-op
  // while contact is null, then refetches when the id resolves. refreshProvenance runs after a
  // human edit so client-side badges re-evaluate.
  const { byField, confirm, confirming, refresh: refreshProvenance } = useProvenance('contact', contact?.id ?? null);
  const badge = (f: string) => (
    <ProvenanceBadge prov={byField[f]} onConfirm={() => confirm(f)} confirming={confirming === f} />
  );
  const [loading, setLoading] = useState(true);
  const [showEdit, setShowEdit] = useState(false);
  // Bumped when the edit modal saves, so the Custom Fields section (which self-fetches
  // and would otherwise show stale values after a modal save) remounts and refetches.
  const [cfVersion, setCfVersion] = useState(0);
  const [showAddDeal, setShowAddDeal] = useState(false);
  const [showAddTask, setShowAddTask] = useState(false);
  const [logActivity, setLogActivity] = useState('');
  const [logNote, setLogNote] = useState('');
  const [logging, setLogging] = useState(false);

  // Publish this contact as the open record for the assistant drawer (issue #14).
  // type+id come from the route param (always current, even mid-load); the loaded name
  // is display-only and only used once it belongs to the CURRENT route — otherwise the
  // chip would show the previous contact's name while its id already points at the new
  // one during a navigation fetch. Falls back to "contact #N" until B loads.
  usePublishActiveRecord('contact', id ? Number(id) : null,
    contact && contact.id === Number(id) ? contact.name : undefined);

  const loadIdRef = useRef(0);
  const load = useCallback(async () => {
    // Guard against a slow response for a previous contact overwriting the
    // current one when navigating quickly between contacts (the delete confirm
    // would otherwise name one contact while deleting the URL's contact).
    const reqId = ++loadIdRef.current;
    setLoading(true);
    try {
      const data = await api<CrmContact>(`/api/crm/contacts/${id}`);
      if (reqId !== loadIdRef.current) return;
      setContact(data);
      // Every path that changes this contact — the edit form, a logged activity, a note —
      // already ends in load(), so notifying here covers them all with one call. The body
      // carries the derived last_contact_at, so the list's column stays truthful too.
      onChanged?.(data);
    } catch (err) {
      if (reqId !== loadIdRef.current) return;
      setContact(null);
      // Opened from a swept list whose copy is stale: a 404 means the record is gone, so
      // the host should drop its row rather than keep offering a dead link.
      if (rowIsGone(err)) onDeleted?.(Number(id));
    }
    if (reqId === loadIdRef.current) setLoading(false);
  }, [id, onChanged, onDeleted]);

  useEffect(() => { queueMicrotask(load); }, [load]);

  async function handleLogActivity() {
    if (!logActivity) return;
    setLogging(true);
    try {
      await api('/api/crm/activity', {
        method: 'POST',
        body: JSON.stringify({ activity: logActivity, note: logNote, contact_id: Number(id) }),
      });
      setLogActivity('');
      setLogNote('');
      load();
    } catch (err) {
      toast.error('Failed to log activity.');
      // An activity row is one of the two signals behind last_contact_at, so a write that
      // may have committed has to be reconciled — load() re-reads the derived value and
      // hands it to the list through onChanged (#77).
      if (writeMayHaveLanded(err)) load();
    } finally {
      setLogging(false);
    }
  }

  async function handleDelete() {
    const ok = await confirmDialog({
      title: 'Delete contact',
      message: `This will permanently delete ${contact?.name || 'this contact'} and their activity history. This cannot be undone.`,
      confirmLabel: 'Delete contact',
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/api/crm/contacts/${id}`, { method: 'DELETE' });
    } catch (err) {
      toast.error('Failed to delete contact.');
      // The DELETE may have committed before the response was lost, in which case the
      // list is still showing a row that no longer exists.
      if (writeMayHaveLanded(err)) onWriteUncertain?.();
      return;
    }
    onDeleted?.(Number(id));
    navigate('/crm/contacts');
  }

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: '80px 0' }}>
        <div className="w-8 h-8 border-2 border-ck-accent border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!contact) return <p style={{ color: INK_MUTE, padding: 32 }}>Contact not found.</p>;

  return (
    <div style={{ padding: isMobile ? '20px 16px' : '32px 44px', maxWidth: 900 }}>
      {/* Back link */}
      <button onClick={() => navigate('/crm/contacts')} style={{
        background: 'none', border: 'none', color: INK_DIM,
        fontSize: 13, cursor: 'pointer', marginBottom: 16,
        display: 'flex', alignItems: 'center', gap: 6,
      }}>
        <IconArrowLeft size={14} strokeWidth={1.85} /> Contacts
      </button>

      {/* Header */}
      <div style={{ marginBottom: isMobile ? 20 : 32 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, minWidth: 0 }}>
            <h1 style={{
              fontFamily: FONT_DISPLAY,
              fontSize: isMobile ? 24 : 32, fontWeight: 400, letterSpacing: '-0.02em',
              color: INK, margin: 0,
            }}>{contact.name}</h1>
            <ScorePill score={contact.lead_score} />
          </div>
          <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
            <button onClick={() => setShowEdit(true)} style={{
              ...btnSecondary, ...btnSmall,
            }}>Edit</button>
            <button onClick={handleDelete} style={{
              ...btnDanger, ...btnSmall,
            }}>Delete</button>
          </div>
        </div>
        {(contact.title || contact.company_id || contact.company) && (
          <p style={{ fontSize: 14, color: INK_MUTE, marginTop: 4 }}>
            {contact.title}
            {contact.title && badge('title')}
            {(contact.company_id || contact.company) && (
              <>
                {contact.title ? ' at ' : ''}
                {contact.company_id ? (
                  <span
                    onClick={() => navigate(`/crm/companies/${contact.company_id}`)}
                    style={{ color: ACCENT_TEXT, cursor: 'pointer' }}
                  >{contact.company_name || contact.company}</span>
                ) : contact.company}
                {' '}{badge('company')}
              </>
            )}
          </p>
        )}
        <div style={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', gap: isMobile ? 4 : 16, marginTop: 8, fontSize: 13, color: INK_MUTE }}>
          {contact.email && <span>{contact.email} {badge('email')}</span>}
          {contact.phone && <span>{contact.phone} {badge('phone')}</span>}
        </div>
        {contact.tags && (
          <div style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            {contact.tags.split(',').map(t => t.trim()).filter(Boolean).map(tag => (
              <span key={tag} style={{
                fontSize: 10, padding: '2px 8px', borderRadius: 3,
                background: BG_RAISED, color: INK_MUTE,
                fontFamily: FONT_MONO,
                letterSpacing: '0.1em',
              }}>{tag}</span>
            ))}
            {badge('tags')}
          </div>
        )}
      </div>

      {/* Notes */}
      {contact.notes && (
        <div style={{
          ...cardStyle,
          padding: isMobile ? 14 : 16, marginBottom: isMobile ? 20 : 24,
        }}>
          <p style={{ ...mono(10), marginBottom: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
            <span>Notes</span> {badge('notes')}
          </p>
          <p style={{ fontSize: 13, color: INK_MUTE, whiteSpace: 'pre-wrap', lineHeight: 1.5, margin: 0 }}>{contact.notes}</p>
        </div>
      )}

      {/* Deals + Tasks */}
      <div style={{ display: isMobile ? 'flex' : 'grid', flexDirection: isMobile ? 'column' : undefined, gridTemplateColumns: isMobile ? undefined : '1fr 1fr', gap: 24 }}>
        {/* Deals */}
        <div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <span style={mono(10, INK_DIM)}>Deals</span>
            <button onClick={() => setShowAddDeal(true)} style={{
              background: 'none', border: 'none', color: ACCENT_TEXT,
              fontSize: 12, cursor: 'pointer',
            }}>+ Add</button>
          </div>
          <div style={{ borderTop: `1px solid ${LINE}` }}>
            {!contact.deals?.length ? (
              <p style={{ color: INK_DIM, fontSize: 12, padding: '16px 0' }}>No deals yet.</p>
            ) : (
              contact.deals.map(d => (
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
                    }}>{d.stage}</span>
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

        {/* Tasks */}
        <div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <span style={mono(10, INK_DIM)}>Tasks</span>
            <button onClick={() => setShowAddTask(true)} style={{
              background: 'none', border: 'none', color: ACCENT_TEXT,
              fontSize: 12, cursor: 'pointer',
            }}>+ Add</button>
          </div>
          <div style={{ borderTop: `1px solid ${LINE}` }}>
            {!contact.tasks?.length ? (
              <p style={{ color: INK_DIM, fontSize: 12, padding: '16px 0' }}>No tasks yet.</p>
            ) : (
              contact.tasks.map(t => (
                <div key={t.id} style={{
                  padding: '10px 0', borderBottom: `1px solid ${LINE}`,
                  display: 'flex', alignItems: 'center', gap: 10,
                  // No `opacity` here (issue #119): it faded the PriorityBadge inside this
                  // row along with everything else, and a badge tuned to just over 4.5:1
                  // cannot survive any fade. The row already reads as done — `ink-dim`
                  // title, line-through, a filled dot — so the opacity was redundant.
                }}>
                  <span style={{
                    width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
                    background: t.completed ? SAGE_FILL : isOverdue(t.due_date) ? CORAL_FILL : INK_DIM,
                  }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <p style={{
                      fontSize: 13, margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      color: t.completed ? INK_DIM : INK,
                      textDecoration: t.completed ? 'line-through' : 'none',
                    }}>{t.title}</p>
                    {t.due_date && (
                      <p style={{
                        fontSize: 11, marginTop: 2,
                        color: isOverdue(t.due_date) && !t.completed ? CORAL_TEXT : INK_DIM,
                      }}>{t.due_date}</p>
                    )}
                  </div>
                  <PriorityBadge priority={t.priority} />
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* Log activity */}
      <div style={{
        marginTop: 24, borderTop: `1px solid ${LINE}`, paddingTop: 24,
      }}>
        <span style={{ ...mono(10, INK_DIM), display: 'block', marginBottom: 12 }}>Log Activity</span>
        <div style={{ display: 'flex', gap: 8, marginBottom: logActivity ? 8 : 0, flexWrap: 'wrap' }}>
          {['call', 'email', 'meeting', 'note'].map(type => (
            <button key={type} onClick={() => setLogActivity(type)} style={{
              padding: '5px 12px', borderRadius: 4, fontSize: 12, textTransform: 'capitalize',
              background: logActivity === type ? ACCENT : 'transparent',
              color: logActivity === type ? ACCENT_INK : INK_MUTE,
              border: logActivity === type ? 'none' : `1px solid ${LINE_STRONG}`,
              cursor: 'pointer',
            }}>{type}</button>
          ))}
        </div>
        {logActivity && (
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <input placeholder="Add a note..." value={logNote} onChange={e => setLogNote(e.target.value)}
              style={{
                ...inputStyle,
                flex: 1, width: undefined, fontSize: 13,
              }}
            />
            <button onClick={handleLogActivity} disabled={logging} style={{
              ...btnPrimary,
              padding: '8px 16px', fontSize: 13,
              opacity: logging ? 0.5 : 1,
            }}>{logging ? 'Saving...' : 'Log'}</button>
          </div>
        )}
      </div>

      {/* Activity history */}
      <div style={{ marginTop: 24, borderTop: `1px solid ${LINE}`, paddingTop: 24 }}>
        <span style={{ ...mono(10, INK_DIM), display: 'block', marginBottom: 12 }}>Activity History</span>
        <ActivityTimeline onUncertainWrite={load} activities={contact.activity || []} onUpdate={load} />
      </div>

      {/* Custom fields — renders nothing when no contact fields are defined */}
      <CustomFieldsSection key={`contact-${contact.id}-${cfVersion}`} entityType="contact" entityId={contact.id} />

      {/* Chatter — editable notes thread */}
      <div style={{ marginTop: 24, borderTop: `1px solid ${LINE}`, paddingTop: 24 }}>
        <span style={{ ...mono(10, INK_DIM), display: 'block', marginBottom: 12 }}>Chatter</span>
        <NotesThread key={`contact-${contact.id}`} entityType="contact" entityId={contact.id}
          // A note is one of the two signals behind last_contact_at, so reload the
          // contact — which is also what tells a host list page to patch its row (#77).
          onChanged={load} />
      </div>

      {showEdit && <ContactForm contact={contact} onWriteUncertain={onWriteUncertain} onClose={() => setShowEdit(false)} onSaved={() => { setShowEdit(false); load(); setCfVersion(v => v + 1); refreshProvenance(); }} />}
      {showAddDeal && <DealForm contactId={contact.id} onClose={() => setShowAddDeal(false)} onSaved={() => { setShowAddDeal(false); load(); }} />}
      {showAddTask && <TaskForm contactId={contact.id} onClose={() => setShowAddTask(false)} onSaved={() => { setShowAddTask(false); load(); }} />}
    </div>
  );
}

function isOverdue(date: string): boolean {
  if (!date) return false;
  return new Date(date) < new Date(new Date().toISOString().split('T')[0]);
}
