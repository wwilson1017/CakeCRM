/**
 * MemoryPage — browse and edit what the assistant knows (issue #72 Phase 2).
 *
 * Until this page existed, the ONLY way to see or change Baker's knowledge was to ask
 * Baker. Two halves: the context FILES (soul.md, MEMORY.md, topic files, daily notes)
 * and the long-term FACTS.
 *
 * This is also the visibility half of the #72 security model. soul.md loads UNFENCED
 * into the system prompt and Baker can rewrite it, so every file shows who wrote it and
 * when — an unexpected identity rewrite should be noticeable, not silent.
 *
 * Fully keyless: files and facts are plain rows, so nothing here needs an AI provider.
 *
 * Ported in spirit from chatty's AgentContextEditor, including its stale-response guard
 * (`loadSeq`) — without it, clicking file A then B can render A's body under B's name
 * whenever the first response lands second.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useIsMobile } from '../shared/useIsMobile';
import { toast } from '../shared/toast';
import { confirmDialog } from '../shared/confirm';
import {
  ACCENT_TEXT, BG_CARD, BG_RAISED, INK, INK_DIM, INK_MUTE, LINE, LINE_STRONG,
  FONT_MONO, tint,
} from '../shared/styles';
import { pagePadding, pageHeading, sectionHeading, cardStyle, btnPrimary, btnSecondary, btnDanger } from './styles';
import {
  type ContextFile, type ContextFileMeta, type MemoryFact,
  listContextFiles, getContextFile, saveContextFile, deleteContextFile,
  listFacts, deleteFact,
} from './memory/api';
import { KIND_LABEL, displayName, writerLabel, sortFiles } from './memory/kindLabel';

type Tab = 'files' | 'facts';

export function MemoryPage() {
  const isMobile = useIsMobile();
  const [tab, setTab] = useState<Tab>('files');

  return (
    <div style={pagePadding(isMobile)}>
      <h1 style={pageHeading(isMobile)}>Memory</h1>
      <p style={{ color: INK_MUTE, marginTop: -8, marginBottom: 20, maxWidth: 640 }}>
        What your assistant knows and carries between conversations. Everything here is
        editable, and works with or without an AI provider configured.
      </p>

      <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
        {(['files', 'facts'] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              ...btnSecondary,
              background: tab === t ? tint(ACCENT_TEXT, 12) : 'transparent',
              color: tab === t ? ACCENT_TEXT : INK_MUTE,
              borderColor: tab === t ? ACCENT_TEXT : LINE,
            }}
          >
            {t === 'files' ? 'Knowledge files' : 'Recorded facts'}
          </button>
        ))}
      </div>

      {tab === 'files' ? <FilesPanel isMobile={isMobile} /> : <FactsPanel isMobile={isMobile} />}
    </div>
  );
}

// ── Files ────────────────────────────────────────────────────────────────────────

function FilesPanel({ isMobile }: { isMobile: boolean }) {
  const [files, setFiles] = useState<ContextFileMeta[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [open, setOpen] = useState<ContextFile | null>(null);
  const [draft, setDraft] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  // Guards against out-of-order responses: only the newest request may render.
  const loadSeq = useRef(0);

  // Declared here, above the callbacks that depend on it, rather than beside the buttons
  // it disables: naming it in a useCallback dep array below its own `const` would read it
  // during render, before initialization — a TDZ crash, not a lint warning.
  const dirty = open !== null && draft !== open.content;

  const refresh = useCallback(async () => {
    try {
      setFiles(sortFiles(await listContextFiles()));
    } catch {
      toast.error('Could not load knowledge files');
    } finally {
      setLoading(false);
    }
  }, []);

  // queueMicrotask, not a bare call: the repo's eslint ruleset rejects setState directly
  // in an effect body (see RemindersPage, same pattern).
  useEffect(() => { queueMicrotask(refresh); }, [refresh]);

  const openFile = useCallback(async (filename: string) => {
    // Opening a file replaces the editor wholesale, so an unsaved edit to whatever is
    // open now would be gone with no undo and nothing on screen to recover it from. Ask
    // first — BEFORE claiming a sequence number, so declining leaves an in-flight save
    // (which also owns loadSeq) able to finish and render.
    if (open && dirty && !(await confirmDialog({
      title: 'Discard unsaved changes?',
      message: `Your edits to ${displayName(open.filename)} have not been saved.`,
      confirmLabel: 'Discard',
      danger: true,
    }))) return;
    const seq = ++loadSeq.current;
    setSelected(filename);
    setOpen(null);
    try {
      const file = await getContextFile(filename);
      if (seq !== loadSeq.current) return;   // a newer click already won
      setOpen(file);
      setDraft(file.content);
    } catch {
      if (seq === loadSeq.current) toast.error(`Could not open ${filename}`);
    }
  }, [dirty, open]);

  const save = useCallback(async () => {
    if (!open) return;
    // Same stale-response guard as openFile: if the user opens another file while this
    // save is in flight, a late response must not replace their editor state and discard
    // the draft they have started.
    const seq = ++loadSeq.current;
    const submitted = draft;
    setSaving(true);
    try {
      const saved = await saveContextFile(open.filename, submitted, open.updated_at);
      if (seq !== loadSeq.current) return;
      // Always take the server's new version token, but only overwrite the editor when
      // the user has not typed since submitting — the textarea stays live during a save,
      // and clobbering it would silently drop those keystrokes.
      setOpen(saved);
      setDraft((current) => (current === submitted ? saved.content : current));
      toast.success(`Saved ${displayName(open.filename)}`);
      void refresh();
    } catch (e) {
      if (seq !== loadSeq.current) return;
      // A 409 means Baker (or another tab) changed the file while it was open. Say so
      // plainly rather than losing whichever version was written second.
      const msg = e instanceof Error ? e.message : 'Save failed';
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  }, [open, draft, refresh]);

  const remove = useCallback(async (file: ContextFileMeta) => {
    const ok = await confirmDialog({
      title: `Delete ${displayName(file.filename)}?`,
      message: 'This permanently removes the file from your assistant\'s knowledge.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteContextFile(file.filename);
      if (selected === file.filename) { setSelected(null); setOpen(null); }
      toast.success('File deleted');
      void refresh();
    } catch {
      toast.error('Could not delete that file');
    }
  }, [selected, refresh]);

  if (loading) return <div style={{ color: INK_MUTE }}>Loading…</div>;

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: isMobile ? '1fr' : 'minmax(240px, 320px) 1fr',
      gap: 20,
      alignItems: 'start',
    }}>
      <div style={cardStyle}>
        <div style={sectionHeading()}>Files</div>
        {files.length === 0 && <div style={{ color: INK_MUTE }}>No files yet.</div>}
        {files.map((f) => (
          <button
            key={f.filename}
            onClick={() => void openFile(f.filename)}
            style={{
              display: 'block', width: '100%', textAlign: 'left', cursor: 'pointer',
              padding: '10px 12px', marginBottom: 6, borderRadius: 8,
              border: `1px solid ${selected === f.filename ? ACCENT_TEXT : LINE}`,
              background: selected === f.filename ? tint(ACCENT_TEXT, 8) : BG_RAISED,
              color: INK, font: 'inherit',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <strong style={{ overflowWrap: 'anywhere' }}>{displayName(f.filename)}</strong>
              <span style={{ color: INK_DIM, fontSize: 12, whiteSpace: 'nowrap' }}>
                {KIND_LABEL[f.kind]}
              </span>
            </div>
            {f.headline && (
              <div style={{ color: INK_MUTE, fontSize: 13, marginTop: 2 }}>{f.headline}</div>
            )}
            <div style={{ color: INK_DIM, fontSize: 12, marginTop: 4 }}>
              Last edited by {writerLabel(f.written_by)}
            </div>
          </button>
        ))}
      </div>

      <div style={cardStyle}>
        {!open && <div style={{ color: INK_MUTE }}>Select a file to view or edit it.</div>}
        {open && (
          <>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
              <div style={sectionHeading()}>{open.filename}</div>
              <div style={{ color: INK_DIM, fontSize: 12 }}>
                {KIND_LABEL[open.kind]} · last edited by {writerLabel(open.written_by)}
                {open.updated_at ? ` · ${new Date(open.updated_at).toLocaleString()}` : ''}
              </div>
            </div>
            {open.kind === 'soul' && (
              <p style={{ color: INK_MUTE, fontSize: 13, marginTop: 0 }}>
                This is your assistant&rsquo;s own description of itself. It can rewrite this
                file, and any change needs your approval first.
              </p>
            )}
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              spellCheck={false}
              style={{
                width: '100%', minHeight: 360, resize: 'vertical', padding: 12,
                borderRadius: 8, border: `1px solid ${LINE_STRONG}`, background: BG_CARD,
                color: INK, fontFamily: FONT_MONO, fontSize: 13, lineHeight: 1.55,
              }}
            />
            <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
              <button style={btnPrimary} onClick={() => void save()} disabled={!dirty || saving}>
                {saving ? 'Saving…' : 'Save'}
              </button>
              <button style={btnSecondary} onClick={() => setDraft(open.content)} disabled={!dirty}>
                Revert
              </button>
              {!open.is_protected && (
                <button
                  style={{ ...btnDanger, marginLeft: 'auto' }}
                  onClick={() => void remove(open as unknown as ContextFileMeta)}
                >
                  Delete
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ── Facts ────────────────────────────────────────────────────────────────────────

function FactsPanel({ isMobile }: { isMobile: boolean }) {
  const [facts, setFacts] = useState<MemoryFact[]>([]);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const loadSeq = useRef(0);

  const refresh = useCallback(async (q: string) => {
    const seq = ++loadSeq.current;
    setLoading(true);
    try {
      const rows = await listFacts(q);
      if (seq !== loadSeq.current) return;
      setFacts(rows);
    } catch {
      if (seq === loadSeq.current) toast.error('Could not load facts');
    } finally {
      if (seq === loadSeq.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => void refresh(search), 250);
    return () => clearTimeout(t);
  }, [search, refresh]);

  const remove = useCallback(async (fact: MemoryFact) => {
    const ok = await confirmDialog({
      title: 'Delete this fact?',
      message: `"${fact.subject} — ${fact.predicate} — ${fact.object}" will be permanently removed.`,
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteFact(fact.id);
      toast.success('Fact deleted');
      void refresh(search);
    } catch {
      toast.error('Could not delete that fact');
    }
  }, [search, refresh]);

  return (
    <div style={cardStyle}>
      <input
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search facts…"
        style={{
          width: '100%', padding: '10px 12px', marginBottom: 16, borderRadius: 8,
          border: `1px solid ${LINE_STRONG}`, background: BG_CARD, color: INK,
        }}
      />
      {loading && <div style={{ color: INK_MUTE }}>Loading…</div>}
      {!loading && facts.length === 0 && (
        <div style={{ color: INK_MUTE }}>
          {search ? 'No facts match that search.' : 'Your assistant has not recorded any facts yet.'}
        </div>
      )}
      {!loading && facts.map((f) => (
        <div
          key={f.id}
          style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12,
            padding: '10px 0', borderBottom: `1px solid ${LINE}`, flexWrap: isMobile ? 'wrap' : 'nowrap',
          }}
        >
          <div style={{ minWidth: 0 }}>
            <div style={{ color: INK, overflowWrap: 'anywhere' }}>
              <strong>{f.subject}</strong> — {f.predicate} — {f.object}
            </div>
            <div style={{ color: INK_DIM, fontSize: 12, marginTop: 2 }}>
              {f.memory_type ? `${f.memory_type} · ` : ''}
              {f.valid_to ? `no longer current (ended ${f.valid_to})` : 'current'}
              {f.archived_at ? ' · archived by nightly cleanup' : ''}
            </div>
          </div>
          <button style={btnDanger} onClick={() => void remove(f)}>Delete</button>
        </div>
      ))}
    </div>
  );
}
