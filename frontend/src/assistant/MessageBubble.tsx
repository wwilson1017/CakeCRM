// CakeCRM — one chat message: user/assistant bubble, tool cards, confirm cards.

import { useState } from 'react';

import { IconBot, IconCheck, IconChevronRight, IconX } from '../shared/icons';
import {
  ACCENT, ACCENT_TEXT,
  ACCENT_INK,
  ACCENT_SOFT,
  BG_CARD,
  BG_RAISED,
  CORAL_TEXT,
  INK,
  INK_MUTE,
  INK_SOFT,
  LINE,
  SAGE_TEXT,
} from '../shared/styles';
import { BulkItemList } from './BulkItemList';
import { bulkItems } from './bulkItems';
import { MarkdownContent } from './MarkdownContent';
import type { ChatMessage, PendingConfirmation, ToolCallInfo, WorkingState } from './types';
import { WorkingIndicator } from './WorkingIndicator';

function summarize(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function ToolCallCard({ call }: { call: ToolCallInfo }) {
  const [open, setOpen] = useState(false);
  const isError = !!(call.result && typeof call.result === 'object' && 'error' in (call.result as object));
  const color = call.status === 'running' ? INK_SOFT : isError ? CORAL_TEXT : SAGE_TEXT;
  return (
    <div style={{ border: `1px solid ${LINE}`, borderRadius: 8, background: BG_RAISED, margin: '6px 0', fontSize: 13 }}>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 6, width: '100%', padding: '6px 10px',
          background: 'none', border: 'none', cursor: 'pointer', color: INK, textAlign: 'left',
        }}
      >
        <span style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform .15s', display: 'inline-flex', color: INK_SOFT }}>
          <IconChevronRight size={14} />
        </span>
        <code style={{ color }}>{call.tool}</code>
        <span style={{ color: INK_SOFT, marginLeft: 'auto' }}>
          {call.status === 'running' ? 'running…' : call.elapsedMs != null ? `${call.elapsedMs} ms` : 'done'}
        </span>
      </button>
      {open && (
        <div style={{ padding: '0 10px 8px 30px', color: INK_MUTE }}>
          {call.args && Object.keys(call.args).length > 0 && (
            <div style={{ marginBottom: 4 }}>
              <span style={{ color: INK_SOFT }}>args: </span>
              <code>{summarize(call.args)}</code>
            </div>
          )}
          {call.result != null && (
            <div>
              <span style={{ color: INK_SOFT }}>result: </span>
              <code style={{ color: isError ? CORAL_TEXT : INK_MUTE }}>{summarize(call.result).slice(0, 800)}</code>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ConfirmationCard({
  confirm,
  onApprove,
  onDeny,
}: {
  confirm: PendingConfirmation;
  onApprove: () => void;
  onDeny: () => void;
}) {
  const args = Object.entries(confirm.args ?? {});
  const items = bulkItems(confirm.args);
  if (confirm.status !== 'pending') {
    const approved = confirm.status === 'approved';
    const failed = confirm.status === 'failed';
    const errMsg =
      failed && confirm.result && typeof confirm.result === 'object'
        ? String((confirm.result as { error?: unknown }).error ?? 'Action failed.')
        : undefined;
    const label = approved ? 'Approved' : failed ? 'Failed' : 'Declined';
    return (
      <div
        style={{
          display: 'flex', flexDirection: 'column', gap: 4, margin: '6px 0', padding: '6px 10px',
          border: `1px solid ${LINE}`, borderRadius: 8, background: BG_RAISED, fontSize: 13,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: approved ? SAGE_TEXT : failed ? CORAL_TEXT : INK_SOFT }}>
          {approved ? <IconCheck size={14} /> : <IconX size={14} />}
          <span>{label}: <code>{confirm.tool}</code></span>
        </div>
        {errMsg && (
          <span style={{ color: INK_SOFT, fontSize: 12, paddingLeft: 20 }}>{errMsg}</span>
        )}
      </div>
    );
  }
  return (
    <div style={{ margin: '8px 0', padding: 12, border: `1px solid ${ACCENT}`, borderRadius: 10, background: BG_CARD }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: INK, marginBottom: 4 }}>
        {confirm.description || `Run ${confirm.tool}?`}
      </div>
      <div style={{ fontSize: 12, color: INK_MUTE, marginBottom: 10 }}>
        <code>{confirm.tool}</code>
        {!items && args.length > 0 && (
          <span> · {args.map(([k, v]) => `${k}: ${summarize(v)}`).join(', ')}</span>
        )}
      </div>
      {items && <BulkItemList items={items} />}
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          onClick={onApprove}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 4, padding: '5px 12px', fontSize: 13, fontWeight: 600,
            background: ACCENT, color: ACCENT_INK, border: 'none', borderRadius: 7, cursor: 'pointer',
          }}
        >
          <IconCheck size={14} /> Approve
        </button>
        <button
          onClick={onDeny}
          style={{
            padding: '5px 12px', fontSize: 13, background: 'none', color: INK_MUTE,
            border: `1px solid ${LINE}`, borderRadius: 7, cursor: 'pointer',
          }}
        >
          Decline
        </button>
      </div>
    </div>
  );
}

export function MessageBubble({
  message,
  working = null,
  onApprove,
  onDeny,
}: {
  message: ChatMessage;
  /** The in-flight turn's state when this is its bubble (#282). */
  working?: WorkingState | null;
  onApprove: (msgId: string, toolUseId: string) => void;
  onDeny: (msgId: string, toolUseId: string) => void;
}) {
  if (message.role === 'user') {
    return (
      <div style={{ display: 'flex', justifyContent: 'flex-end', margin: '10px 0' }}>
        <div
          style={{
            maxWidth: '85%', padding: '8px 12px', borderRadius: 12, background: ACCENT_SOFT,
            color: INK, fontSize: 14, lineHeight: 1.5, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
          }}
        >
          {message.content}
        </div>
      </div>
    );
  }

  const empty = !message.content && !(message.toolCalls?.length) && !(message.pendingConfirmations?.length);
  return (
    <div style={{ display: 'flex', gap: 8, margin: '10px 0' }}>
      <div
        style={{
          flexShrink: 0, width: 26, height: 26, borderRadius: 7, background: ACCENT_SOFT,
          color: ACCENT_TEXT, display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}
      >
        <IconBot size={16} />
      </div>
      <div style={{ minWidth: 0, flex: 1 }}>
        {empty && message.streaming && !working ? (
          <span style={{ color: INK_SOFT, fontSize: 14 }}>…</span>
        ) : (
          message.content && <MarkdownContent content={message.content} />
        )}
        {message.toolCalls?.map((c, i) => (
          // index key, not toolUseId — positional-id providers (Gemini) reuse ids
          // within one message, which would collide as React keys.
          <ToolCallCard key={i} call={c} />
        ))}
        {message.pendingConfirmations?.map((c, i) => (
          <ConfirmationCard
            key={`${c.toolUseId}-${i}`}
            confirm={c}
            onApprove={() => onApprove(message.id, c.toolUseId)}
            onDeny={() => onDeny(message.id, c.toolUseId)}
          />
        ))}
        {working && <WorkingIndicator working={working} />}
      </div>
    </div>
  );
}
