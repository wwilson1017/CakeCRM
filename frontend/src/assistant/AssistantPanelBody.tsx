// CakeCRM — the assistant chat surface (issue #4).
//
// Self-contained, compact-panel-sized unit that #9's AssistantLauncher mounts
// into its panel body. Assumes ai_ready === true (the launcher only renders it
// when a provider is configured) and self-manages conversations + streaming.

import { useEffect, useMemo, useRef, useState } from 'react';

import { IconAttach, IconBot, IconPlus, IconSettings, IconX } from '../shared/icons';
import { toast } from '../shared/toast';
import {
  ACCENT,
  ACCENT_INK,
  ACCENT_SOFT,
  BG_CARD,
  BG_RAISED,
  CORAL,
  GOLD,
  INK,
  INK_MUTE,
  INK_SOFT,
  LINE,
  SAGE,
} from '../shared/styles';
import { IdentitySettings } from './IdentitySettings';
import { MessageBubble } from './MessageBubble';
import { QuickActions } from './QuickActions';
import type { ActiveRecordContext, ToolMode } from './types';
import { useAssistantChat } from './useAssistantChat';
import { useConversations } from './useConversations';

// Client-side pre-check mirrors backend/assistant/uploads.py (ALLOWED_EXTENSIONS
// / MAX_FILES / MAX_FILE_SIZE). Keep in sync — the backend re-validates and is
// authoritative, so a drift here only affects the pre-submit UX, never safety.
const ALLOWED = ['csv', 'xlsx', 'md', 'txt', 'pdf', 'docx'];
const MAX_FILES = 5;
const MAX_BYTES = 10 * 1024 * 1024;

const MODES: { mode: ToolMode; label: string; title: string }[] = [
  { mode: 'read-only', label: 'Read', title: 'Read-only — the assistant can look things up but not change data' },
  { mode: 'normal', label: 'Ask', title: 'Ask first — changes need your approval' },
  { mode: 'power', label: 'Auto', title: 'Auto — changes run without asking' },
];

export interface AssistantPanelBodyProps {
  /** CRM record open behind this surface; null/omitted → generic panel. */
  recordContext?: ActiveRecordContext | null;
}

export default function AssistantPanelBody({ recordContext = null }: AssistantPanelBodyProps = {}) {
  const chat = useAssistantChat(recordContext);
  const { conversations, load: loadConversations, openConversation: fetchConversation, remove: removeConversation } = useConversations();
  const [input, setInput] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [showSettings, setShowSettings] = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  const endRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    void loadConversations();
  }, [loadConversations]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' });
  }, [chat.messages]);

  const stageFiles = (incoming: FileList | null) => {
    if (!incoming) return;
    const next = [...files];
    for (const f of Array.from(incoming)) {
      const ext = f.name.includes('.') ? f.name.split('.').pop()!.toLowerCase() : '';
      if (!ALLOWED.includes(ext)) {
        toast.error(`${f.name}: unsupported type. Allowed: ${ALLOWED.join(', ')}.`);
        continue;
      }
      if (f.size > MAX_BYTES) {
        toast.error(`${f.name}: exceeds 10 MB.`);
        continue;
      }
      if (next.length >= MAX_FILES) {
        toast.error(`At most ${MAX_FILES} files.`);
        break;
      }
      next.push(f);
    }
    setFiles(next);
    if (fileRef.current) fileRef.current.value = '';
  };

  const submit = () => {
    const text = input.trim();
    if ((!text && files.length === 0) || chat.isStreaming) return;
    chat.sendMessage(text, files.length ? files : undefined);
    setInput('');
    setFiles([]);
    if (taRef.current) taRef.current.style.height = 'auto';
  };

  const newChat = () => {
    chat.clear();
    setShowHistory(false);
  };

  const openConversation = async (id: string) => {
    setShowHistory(false);
    const conv = await fetchConversation(id);
    if (conv) chat.loadMessages(conv.messages, conv.id);
  };

  const meterPct = useMemo(() => {
    if (!chat.contextUsage || !chat.contextUsage.contextWindow) return null;
    return Math.min(100, Math.round((chat.contextUsage.contextTokens / chat.contextUsage.contextWindow) * 100));
  }, [chat.contextUsage]);
  const meterColor = meterPct == null ? INK_SOFT : meterPct >= 90 ? CORAL : meterPct >= 75 ? GOLD : SAGE;

  return (
    <div style={{ position: 'relative', display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, background: BG_CARD, color: INK }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 10px', borderBottom: `1px solid ${LINE}` }}>
        <button
          // Refetch on OPEN: the drawer now stays mounted for the whole session
          // (issue #14), so the mount-time load() no longer runs per open — without
          // this the list would miss conversations created since the shell loaded.
          onClick={() => { if (!showHistory) void loadConversations(); setShowHistory((s) => !s); }}
          style={{ display: 'flex', alignItems: 'center', gap: 6, background: 'none', border: 'none', cursor: 'pointer', color: INK, fontSize: 13, fontWeight: 600, padding: 4, minWidth: 0 }}
        >
          <IconBot size={16} />
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>Conversations</span>
        </button>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 2 }}>
          <button onClick={newChat} title="New chat" style={iconBtn}><IconPlus size={17} /></button>
          <button onClick={() => setShowSettings(true)} title="Assistant settings" style={iconBtn}><IconSettings size={17} /></button>
        </div>
      </div>

      {showHistory && (
        <div style={{ position: 'absolute', top: 44, left: 8, right: 8, zIndex: 1, maxHeight: '60%', overflowY: 'auto', background: BG_CARD, border: `1px solid ${LINE}`, borderRadius: 10, boxShadow: '0 8px 24px rgba(0,0,0,0.12)' }}>
          {conversations.length === 0 ? (
            <div style={{ padding: 12, color: INK_MUTE, fontSize: 13 }}>No conversations yet.</div>
          ) : (
            conversations.map((c) => (
              <div key={c.id} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 10px', borderBottom: `1px solid ${LINE}` }}>
                <button
                  onClick={() => openConversation(c.id)}
                  style={{ flex: 1, minWidth: 0, textAlign: 'left', background: 'none', border: 'none', cursor: 'pointer', color: INK, fontSize: 13 }}
                >
                  <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.title}</div>
                </button>
                <button onClick={() => removeConversation(c.id)} title="Delete" style={{ ...iconBtn, color: INK_SOFT }}><IconX size={15} /></button>
              </div>
            ))
          )}
        </div>
      )}

      {/* Messages */}
      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '4px 12px' }}>
        {chat.messages.length === 0 ? (
          <div style={{ height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: INK_MUTE, textAlign: 'center', gap: 8, padding: 20 }}>
            <IconBot size={28} />
            <div style={{ fontSize: 14 }}>Ask me about your contacts, deals, and tasks — or drop in a file.</div>
          </div>
        ) : (
          chat.messages.map((m) => (
            <MessageBubble key={m.id} message={m} onApprove={chat.approveAction} onDeny={chat.denyAction} />
          ))
        )}
        <div ref={endRef} />
      </div>

      {/* Composer */}
      <div style={{ borderTop: `1px solid ${LINE}`, padding: 8 }}>
        {recordContext && (
          <QuickActions
            record={recordContext}
            onPick={(p) => { if (!chat.isStreaming) chat.sendMessage(p); }}
            disabled={chat.isStreaming}
          />
        )}

        {meterPct != null && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6, fontSize: 11, color: INK_SOFT }}>
            <div style={{ flex: 1, height: 3, background: BG_RAISED, borderRadius: 2, overflow: 'hidden' }}>
              <div style={{ width: `${meterPct}%`, height: '100%', background: meterColor }} />
            </div>
            <span>{meterPct}% context</span>
          </div>
        )}

        {files.length > 0 && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 6 }}>
            {files.map((f, i) => (
              <span key={`${f.name}-${i}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, background: BG_RAISED, border: `1px solid ${LINE}`, borderRadius: 6, padding: '2px 6px', color: INK_MUTE }}>
                {f.name}
                <button onClick={() => setFiles(files.filter((_, j) => j !== i))} style={{ background: 'none', border: 'none', cursor: 'pointer', color: INK_SOFT, display: 'flex' }}><IconX size={12} /></button>
              </span>
            ))}
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 6 }}>
          <input ref={fileRef} type="file" multiple accept={ALLOWED.map((e) => `.${e}`).join(',')} onChange={(e) => stageFiles(e.target.files)} style={{ display: 'none' }} />
          <button onClick={() => fileRef.current?.click()} title="Attach a file" style={iconBtn}><IconAttach size={18} /></button>
          <textarea
            ref={taRef}
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              e.target.style.height = 'auto';
              e.target.style.height = `${Math.min(e.target.scrollHeight, 120)}px`;
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder="Message the assistant…"
            rows={1}
            style={{ flex: 1, resize: 'none', border: `1px solid ${LINE}`, borderRadius: 10, padding: '8px 10px', fontSize: 14, fontFamily: 'inherit', color: INK, background: BG_CARD, maxHeight: 120, outline: 'none' }}
          />
          {chat.isStreaming ? (
            <button onClick={chat.stop} title="Stop" style={{ ...sendBtn, background: BG_RAISED, color: INK_MUTE }}>■</button>
          ) : (
            <button onClick={submit} title="Send" disabled={!input.trim() && files.length === 0} style={{ ...sendBtn, opacity: !input.trim() && files.length === 0 ? 0.5 : 1 }}>
              <IconArrowUpInline />
            </button>
          )}
        </div>

        {/* Tool mode */}
        <div style={{ display: 'flex', gap: 4, marginTop: 6 }}>
          {MODES.map((m) => {
            const active = chat.toolMode === m.mode;
            return (
              <button
                key={m.mode}
                onClick={() => chat.setToolMode(m.mode)}
                title={m.title}
                style={{
                  flex: 1, fontSize: 11, padding: '4px 6px', borderRadius: 6, cursor: 'pointer',
                  border: `1px solid ${active ? ACCENT : LINE}`,
                  background: active ? ACCENT_SOFT : 'none',
                  color: active ? ACCENT : INK_MUTE,
                  fontWeight: active ? 600 : 400,
                }}
              >
                {m.label}
              </button>
            );
          })}
        </div>
      </div>

      {showSettings && <IdentitySettings onClose={() => setShowSettings(false)} />}
    </div>
  );
}

const iconBtn: React.CSSProperties = {
  display: 'flex', alignItems: 'center', justifyContent: 'center', width: 30, height: 30,
  background: 'none', border: 'none', cursor: 'pointer', color: INK_MUTE, borderRadius: 7,
};

const sendBtn: React.CSSProperties = {
  display: 'flex', alignItems: 'center', justifyContent: 'center', width: 32, height: 32,
  background: ACCENT, color: ACCENT_INK, border: 'none', borderRadius: 9, cursor: 'pointer', flexShrink: 0,
};

function IconArrowUpInline() {
  return (
    <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 19V5M5 12l7-7 7 7" />
    </svg>
  );
}
