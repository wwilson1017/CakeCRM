import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../../core/api/client';
import { writeMayHaveLanded } from '../usePatchableAssembly';
import type { CrmNote } from '../../core/types';
import {
  mono, INK, INK_MUTE, INK_DIM, LINE, LINE_STRONG, ACCENT, ACCENT_INK, ACCENT_TEXT, inputStyle,
} from '../../shared/styles';
import { toast } from '../../shared/toast';
import { formatDate } from '../../shared/formatDate';
import { MAX_NOTE_LEN } from '../chatterComposer';
import { mentionSegments } from '../chatterMentions';
import { useChatterPost } from '../useChatterPost';
import { NoteComposer } from './NoteComposer';
import { NoteAttachments } from './NoteAttachments';
import { useMentionPicker } from './MentionPicker';

interface Props {
  entityType: 'deal' | 'contact' | 'company';
  entityId: number;
  /**
   * Fired after a note is added, edited, archived or restored (#77).
   *
   * A contact's notes are one of the two signals behind its derived `last_contact_at`,
   * so a host that renders that value has to be told: adding a note should update it,
   * archiving the newest one should reveal the previous timestamp, and restoring it
   * should put the newer one back. Optional — the deal and company call sites ignore it.
   */
  onChanged?: () => void;
}

/**
 * Chatter — the editable, archivable notes thread for a deal, contact, or company
 * (companies joined in issue #22), shown alongside the activity timeline.
 * Self-fetches from /api/crm/chatter and owns its own compose / edit / archive state
 * so it drops into the contact page, the company page, or the pipeline deal sheet
 * unchanged.
 */
export function NotesThread({ entityType, entityId, onChanged }: Props) {
  const [notes, setNotes] = useState<CrmNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);

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

  // #57's compose flow carrying #77's semantics. The old inline `addNote` is gone — the
  // post-then-upload sequence lives in useChatterPost now — but both of #77's rules still
  // apply and are threaded through here rather than lost with it.
  //
  // Rule 1: every mutation notifies the host, because a contact's notes feed its derived
  // `last_contact_at`.
  const reload = useCallback(() => { load(); onChanged?.(); }, [load, onChanged]);

  // Rule 2: a FAILED create may still have committed, so reload before re-throwing. The
  // re-throw is load-bearing in the other direction (#57): useChatterPost lets a create
  // failure reject so the composer keeps the text and files the user is about to lose.
  // A definite 4xx wrote nothing.
  const createNote = useCallback(
    async (message: string, mentions: number[]) => {
      try {
        // The route already returns the created row, which is what gives the attachment
        // uploads a note id to aim at. Mentions (#235) ride only when there are some.
        return await api<{ id: number }>(
          `/api/crm/chatter/${entityType}/${entityId}/note`,
          {
            method: 'POST',
            body: JSON.stringify(mentions.length ? { message, mentions } : { message }),
          },
        );
      } catch (err) {
        toast.error('Failed to add note.');
        if (writeMayHaveLanded(err)) reload();
        throw err;
      }
    },
    [entityType, entityId, reload],
  );

  // Retry state names a note id on a SPECIFIC record, so the hook is keyed to the entity:
  // the deal sheet does not remount between deals, and a stale Retry button would file a
  // photo onto the previous deal's note.
  const { post, retryFiles, retry, discardRetry, notice } = useChatterPost(
    `${entityType}:${entityId}`, createNote, reload,
  );

  async function saveEdit(id: number, text: string, mentions: number[]) {
    if (!text.trim()) return;
    try {
      // An edit always sends its mention list (#235): the server treats a list as the new
      // set, so deleting an `@name` token un-mentions that person, and only people newly
      // added are notified. Omitting the field would preserve the old set instead.
      await api(`/api/crm/chatter/note/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ message: text.trim(), mentions }),
      });
      setEditingId(null);
      reload();
    } catch (err) {
      toast.error('Failed to save note.');
      // The write may still have committed and moved this contact's last_contact_at, so
      // reload rather than leave the thread — and the host's column — showing the old
      // state. A definite 4xx wrote nothing (#77).
      if (writeMayHaveLanded(err)) reload();
    }
  }

  async function setArchived(id: number, archived: boolean) {
    try {
      await api(`/api/crm/chatter/note/${id}/${archived ? 'archive' : 'unarchive'}`, { method: 'POST' });
      reload();
    } catch (err) {
      toast.error(`Failed to ${archived ? 'archive' : 'restore'} note.`);
      // See createNote: archiving the newest note changes last_contact_at too.
      if (writeMayHaveLanded(err)) reload();
    }
  }

  return (
    <div>
      {/* Composer (#57) — a ~6-line auto-growing box that also takes attachments by
          paste, drop or the Attach button. The post-then-upload sequence and its
          partial-failure retry live in useChatterPost.

          The `key` is load-bearing, not tidiness. The composer owns the draft text and the
          staged files, and the deal sheet does NOT remount between deals — so without it,
          typing a note on deal A, switching to deal B and pressing Post would file A's
          words and A's screenshot onto B. Keying on the entity discards the draft with the
          record it belongs to (and unmounts the staged previews, freeing their object
          URLs). useChatterPost's own guard covers the retry batch; this covers the draft,
          which that guard cannot see. */}
      <NoteComposer
        key={`${entityType}:${entityId}`}
        onSubmit={post}
        retryFiles={retryFiles}
        onRetry={retry}
        onDiscardRetry={discardRetry}
        notice={notice}
      />

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
                <NoteEditor
                  note={n}
                  onSave={(text, mentions) => saveEdit(n.id, text, mentions)}
                  onCancel={() => setEditingId(null)}
                />
              ) : (
                <>
                  <p style={{ fontSize: 13, color: INK, margin: 0, whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>
                    {mentionSegments(n.message, (n.mentions ?? []).map(m => m.name)).map((seg, i) => (
                      seg.mention
                        // #235: a mention reads as the person's name in the accent ink —
                        // weight and colour, no tinted chip, so nothing new is owed to the
                        // contrast guards (ACCENT_TEXT on the card is already pinned).
                        ? <span key={i} style={{ color: ACCENT_TEXT, fontWeight: 600 }}>{seg.text}</span>
                        : <span key={i}>{seg.text}</span>
                    ))}
                  </p>
                  {n.attachments && n.attachments.length > 0 && (
                    <NoteAttachments items={n.attachments} onChanged={reload} />
                  )}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 4, flexWrap: 'wrap' }}>
                    <span style={{ ...mono(10), color: INK_DIM }}>{formatDate(n.created_at)}</span>
                    {n.updated_at && <span style={{ ...mono(10), color: INK_DIM }}>· edited</span>}
                    {!!n.archived && <span style={{ ...mono(10), color: INK_DIM }}>· archived</span>}
                    {!n.archived && (
                      <button onClick={() => setEditingId(n.id)} style={linkBtn}>Edit</button>
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

/**
 * The inline edit box. Its own component so each edit gets its own @ picker (#235),
 * seeded from the note's stored mentions — which carry the name frozen at post time, so
 * a person deactivated since stays mentioned rather than dropping off on an unrelated edit.
 */
function NoteEditor({ note, onSave, onCancel }: {
  note: CrmNote;
  onSave: (text: string, mentions: number[]) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState(note.message);
  const ref = useRef<HTMLTextAreaElement>(null);
  const mention = useMentionPicker(
    text, setText, ref, (note.mentions ?? []).map(m => ({ id: m.user_id, label: m.name })),
  );
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ position: 'relative', display: 'flex', flexDirection: 'column' }}>
        <textarea
          ref={ref}
          aria-label="Edit note"
          value={text}
          onChange={e => { setText(e.target.value); mention.onChange(e.target); }}
          {...mention.textareaProps}
          onKeyDown={e => { mention.onKeyDown(e); }}
          rows={2}
          maxLength={MAX_NOTE_LEN}
          style={{ ...inputStyle, width: undefined, fontSize: 13, resize: 'vertical' }}
        />
        {mention.menu}
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <button onClick={() => onSave(text, mention.mentionIds())} style={miniBtn(true)}>Save</button>
        <button onClick={onCancel} style={miniBtn(false)}>Cancel</button>
      </div>
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
