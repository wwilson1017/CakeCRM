import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../../core/api/client';
import type { CrmNote } from '../../core/types';
import { mono, INK, INK_MUTE, INK_DIM, LINE, LINE_STRONG, ACCENT, ACCENT_INK, inputStyle } from '../../shared/styles';
import { toast } from '../../shared/toast';
import { formatDate } from '../../shared/formatDate';

interface Props {
  entityType: 'deal' | 'contact';
  entityId: number;
}

/**
 * Chatter — the editable, archivable notes thread for a deal or contact, shown
 * alongside the activity timeline. Self-fetches from /api/crm/chatter and owns
 * its own compose / edit / archive state so it can drop into either the contact
 * detail page or the pipeline deal sheet unchanged.
 */
export function NotesThread({ entityType, entityId }: Props) {
  const [notes, setNotes] = useState<CrmNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [draft, setDraft] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editText, setEditText] = useState('');

  const reqRef = useRef(0);
  const load = useCallback(async () => {
    const reqId = ++reqRef.current;
    setLoading(true);
    try {
      const data = await api<{ notes: CrmNote[] }>(
        `/api/crm/chatter/${entityType}/${entityId}?limit=200&include_archived=${showArchived}`,
      );
      if (reqId !== reqRef.current) return;
      setNotes(data.notes);
      setError(false);
    } catch {
      if (reqId !== reqRef.current) return;
      setError(true);
    }
    if (reqId === reqRef.current) setLoading(false);
  }, [entityType, entityId, showArchived]);

  // queueMicrotask defers the setLoading(true) out of the synchronous effect body
  // (matches ContactDetailPage; satisfies react-hooks/set-state-in-effect).
  useEffect(() => { queueMicrotask(load); }, [load]);

  async function addNote() {
    if (!draft.trim() || submitting) return;
    setSubmitting(true);
    try {
      await api(`/api/crm/chatter/${entityType}/${entityId}/note`, {
        method: 'POST',
        body: JSON.stringify({ message: draft.trim() }),
      });
      setDraft('');
      load();
    } catch {
      toast.error('Failed to add note.');
    } finally {
      setSubmitting(false);
    }
  }

  async function saveEdit(id: number) {
    if (!editText.trim()) return;
    try {
      await api(`/api/crm/chatter/note/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ message: editText.trim() }),
      });
      setEditingId(null);
      setEditText('');
      load();
    } catch {
      toast.error('Failed to save note.');
    }
  }

  async function setArchived(id: number, archived: boolean) {
    try {
      await api(`/api/crm/chatter/note/${id}/${archived ? 'archive' : 'unarchive'}`, { method: 'POST' });
      load();
    } catch {
      toast.error(`Failed to ${archived ? 'archive' : 'restore'} note.`);
    }
  }

  return (
    <div>
      {/* Composer */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'flex-start' }}>
        <textarea
          placeholder="Add a note…"
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') addNote(); }}
          rows={2}
          style={{ ...inputStyle, flex: 1, width: undefined, fontSize: 13, resize: 'vertical', minHeight: 38 }}
        />
        <button onClick={addNote} disabled={submitting || !draft.trim()} style={{
          background: ACCENT, color: ACCENT_INK, border: 'none',
          padding: '8px 16px', borderRadius: 4, fontSize: 13, fontWeight: 500,
          cursor: submitting || !draft.trim() ? 'default' : 'pointer',
          opacity: submitting || !draft.trim() ? 0.5 : 1, flexShrink: 0,
        }}>{submitting ? 'Saving…' : 'Add'}</button>
      </div>

      {/* Show-archived toggle — always rendered so archived notes stay reachable
          even when every active note has been archived (which would otherwise
          leave the current filter's `notes` empty and hide the toggle). */}
      <button onClick={() => setShowArchived(v => !v)} style={{
        background: 'none', border: 'none', color: INK_DIM,
        fontSize: 12, cursor: 'pointer', padding: 0, marginBottom: 10,
      }}>{showArchived ? 'Hide archived' : 'Show archived'}</button>

      {/* List */}
      {loading && notes.length === 0 ? (
        <p style={{ color: INK_DIM, fontSize: 13 }}>Loading…</p>
      ) : error ? (
        <p style={{ color: INK_DIM, fontSize: 13 }}>Couldn't load notes.</p>
      ) : notes.length === 0 ? (
        <p style={{ color: INK_DIM, fontSize: 13 }}>No notes yet.</p>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {notes.map(n => (
            <div key={n.id} style={{
              borderLeft: `2px solid ${LINE_STRONG}`, paddingLeft: 12,
              opacity: n.archived ? 0.55 : 1,
            }}>
              {editingId === n.id ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <textarea
                    value={editText}
                    onChange={e => setEditText(e.target.value)}
                    rows={2}
                    style={{ ...inputStyle, width: undefined, fontSize: 13, resize: 'vertical' }}
                  />
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button onClick={() => saveEdit(n.id)} style={miniBtn(true)}>Save</button>
                    <button onClick={() => { setEditingId(null); setEditText(''); }} style={miniBtn(false)}>Cancel</button>
                  </div>
                </div>
              ) : (
                <>
                  <p style={{ fontSize: 13, color: INK, margin: 0, whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>
                    {n.message}
                  </p>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 4, flexWrap: 'wrap' }}>
                    <span style={{ ...mono(10), color: INK_DIM }}>{formatDate(n.created_at)}</span>
                    {n.updated_at && <span style={{ ...mono(10), color: INK_DIM }}>· edited</span>}
                    {!!n.archived && <span style={{ ...mono(10), color: INK_DIM }}>· archived</span>}
                    {!n.archived && (
                      <button onClick={() => { setEditingId(n.id); setEditText(n.message); }} style={linkBtn}>Edit</button>
                    )}
                    <button onClick={() => setArchived(n.id, !n.archived)} style={linkBtn}>
                      {n.archived ? 'Restore' : 'Archive'}
                    </button>
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const linkBtn = {
  background: 'none', border: 'none', color: INK_MUTE,
  fontSize: 11, cursor: 'pointer', padding: 0,
} as const;

function miniBtn(primary: boolean) {
  return {
    padding: '5px 12px', borderRadius: 4, fontSize: 12, cursor: 'pointer',
    border: primary ? 'none' : `1px solid ${LINE}`,
    background: primary ? ACCENT : 'transparent',
    color: primary ? ACCENT_INK : INK_MUTE,
  } as const;
}
