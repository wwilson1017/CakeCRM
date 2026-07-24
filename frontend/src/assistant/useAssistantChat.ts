// CakeCRM — assistant streaming chat hook.
//
// Consumes the /api/assistant SSE stream over a POST + fetch ReadableStream
// (get_current_user reads a Bearer header, so a browser EventSource can't be
// used). Text deltas are rAF-batched; tool/confirm events update the in-flight
// assistant message. Write confirmations are server-authoritative: approve/deny
// POST /confirm, and once a message's last pending card resolves we re-POST an
// empty-messages continuation so the model finishes the turn.

import { useCallback, useRef, useState } from 'react';

import { getToken, TOKEN_KEY } from '../core/auth/tokenUtils';
import { toast } from '../shared/toast';
import type {
  ChatMessage,
  ContextUsage,
  ServerMessage,
  ToolCallInfo,
  ToolMode,
} from './types';

const API = '/api/assistant';

type SSEEvent = Record<string, unknown>;

function newId(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

// ── Message transforms (pure) ────────────────────────────────────────────────

function addToolCall(m: ChatMessage, evt: SSEEvent): ChatMessage {
  const call: ToolCallInfo = {
    tool: String(evt.tool ?? ''),
    toolUseId: String(evt.tool_use_id ?? ''),
    status: 'running',
  };
  return { ...m, toolCalls: [...(m.toolCalls ?? []), call] };
}

function patchCall(m: ChatMessage, toolUseId: string, patch: Partial<ToolCallInfo>): ChatMessage {
  const calls = m.toolCalls ?? [];
  // A positional-id provider (Gemini) reuses tool_use_id across iterations that
  // all stream into this one message, so patch only the LAST still-running card
  // with that id (the one this tool_args/tool_end belongs to), not every match.
  let target = -1;
  for (let i = calls.length - 1; i >= 0; i--) {
    if (calls[i].toolUseId === toolUseId && calls[i].status === 'running') {
      target = i;
      break;
    }
  }
  if (target === -1) {
    for (let i = calls.length - 1; i >= 0; i--) {
      if (calls[i].toolUseId === toolUseId) {
        target = i;
        break;
      }
    }
  }
  if (target === -1) return m;
  return { ...m, toolCalls: calls.map((c, i) => (i === target ? { ...c, ...patch } : c)) };
}

function addConfirm(m: ChatMessage, evt: SSEEvent): ChatMessage {
  const existing = m.pendingConfirmations ?? [];
  const toolUseId = String(evt.tool_use_id ?? '');
  if (existing.some((c) => c.toolUseId === toolUseId && c.status === 'pending')) return m;
  // The provider streams the tool_use block (tool_start/tool_args) BEFORE the
  // engine decides to gate it, so a "running…" tool card was already added. The
  // confirmation card now represents this call — drop that ONE spinning card
  // (last running with this id; earlier same-id cards from a positional-id
  // provider stay).
  const calls = m.toolCalls ?? [];
  let drop = -1;
  for (let i = calls.length - 1; i >= 0; i--) {
    if (calls[i].toolUseId === toolUseId && calls[i].status === 'running') {
      drop = i;
      break;
    }
  }
  return {
    ...m,
    toolCalls: drop === -1 ? calls : calls.filter((_, i) => i !== drop),
    pendingConfirmations: [
      ...existing,
      {
        tool: String(evt.tool ?? ''),
        toolUseId,
        msgId: evt.msg_id ? String(evt.msg_id) : undefined,
        args: (evt.args as Record<string, unknown>) ?? {},
        description: evt.description ? String(evt.description) : undefined,
        status: 'pending',
      },
    ],
  };
}

export function useAssistantChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [toolMode, setToolMode] = useState<ToolMode>('normal');
  const [contextUsage, setContextUsage] = useState<ContextUsage | null>(null);

  const messagesRef = useRef<ChatMessage[]>([]);
  const convIdRef = useRef<string | null>(null);
  const toolModeRef = useRef<ToolMode>('normal');
  const abortRef = useRef<AbortController | null>(null);
  const userAbortedRef = useRef(false);
  const textBufRef = useRef<Record<string, string>>({});
  const rafRef = useRef<number | null>(null);

  const commit = useCallback((next: ChatMessage[]) => {
    messagesRef.current = next;
    setMessages(next);
  }, []);

  const updateMessage = useCallback((id: string, fn: (m: ChatMessage) => ChatMessage) => {
    const next = messagesRef.current.map((m) => (m.id === id ? fn(m) : m));
    messagesRef.current = next;
    setMessages(next);
  }, []);

  const flushText = useCallback((id: string) => {
    const buf = textBufRef.current[id];
    if (buf != null) updateMessage(id, (m) => ({ ...m, content: buf }));
  }, [updateMessage]);

  const scheduleFlush = useCallback((id: string) => {
    if (rafRef.current != null) return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null;
      flushText(id);
    });
  }, [flushText]);

  const handleEvent = useCallback((evt: SSEEvent, asstId: string) => {
    switch (evt.type) {
      case 'conversation_id': {
        const id = String(evt.id ?? '');
        convIdRef.current = id;
        setConversationId(id);
        break;
      }
      case 'text':
        textBufRef.current[asstId] = (textBufRef.current[asstId] ?? '') + String(evt.text ?? '');
        scheduleFlush(asstId);
        break;
      case 'tool_start':
        updateMessage(asstId, (m) => addToolCall(m, evt));
        break;
      case 'tool_args':
        updateMessage(asstId, (m) =>
          patchCall(m, String(evt.tool_use_id ?? ''), {
            args: (evt.args as Record<string, unknown>) ?? {},
            description: evt.description ? String(evt.description) : undefined,
          }),
        );
        break;
      case 'tool_end':
        updateMessage(asstId, (m) =>
          patchCall(m, String(evt.tool_use_id ?? ''), {
            result: evt.result,
            status: 'done',
            elapsedMs: typeof evt.elapsed_ms === 'number' ? evt.elapsed_ms : undefined,
          }),
        );
        break;
      case 'confirm':
        updateMessage(asstId, (m) => addConfirm(m, evt));
        break;
      case 'usage':
        setContextUsage({
          contextTokens: Number(evt.context_tokens ?? 0),
          contextWindow: Number(evt.context_window ?? 0),
        });
        break;
      case 'done':
        updateMessage(asstId, (m) => ({ ...m, model: evt.model ? String(evt.model) : m.model, streaming: false }));
        break;
      case 'error': {
        // Append the error INTO the text buffer so the final flush (which sets
        // content = buffer) can't overwrite it — an error before any text would
        // otherwise render as an empty bubble.
        const prefix = textBufRef.current[asstId] ? textBufRef.current[asstId] + '\n\n' : '';
        textBufRef.current[asstId] = prefix + `⚠️ ${String(evt.error ?? 'Something went wrong.')}`;
        flushText(asstId);
        updateMessage(asstId, (m) => ({ ...m, streaming: false, error: true }));
        break;
      }
      default:
        break;
    }
  }, [flushText, scheduleFlush, updateMessage]);

  const runStream = useCallback(async (body: BodyInit, isForm: boolean, asstId: string) => {
    const controller = new AbortController();
    abortRef.current = controller;
    const token = getToken();
    const headers: Record<string, string> = {};
    if (!isForm) headers['Content-Type'] = 'application/json';
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const endpoint = isForm ? `${API}/chat/upload` : `${API}/chat`;
    let res: Response;
    try {
      res = await fetch(endpoint, { method: 'POST', headers, body, signal: controller.signal });
    } catch {
      updateMessage(asstId, (m) => ({ ...m, content: '⚠️ Network error — is the server running?', streaming: false, error: true }));
      setIsStreaming(false);
      abortRef.current = null;
      return;
    }

    if (res.status === 401) {
      sessionStorage.removeItem(TOKEN_KEY);
      window.location.href = '/login';
      return;
    }
    if (!res.ok || !res.body) {
      let detail = 'Request failed.';
      try {
        const b = await res.json();
        if (b?.detail) detail = String(b.detail);
      } catch { /* not JSON */ }
      updateMessage(asstId, (m) => ({ ...m, content: `⚠️ ${detail}`, streaming: false, error: true }));
      setIsStreaming(false);
      abortRef.current = null;
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() ?? '';
        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith('data:')) continue;
          try {
            handleEvent(JSON.parse(line.slice(line.indexOf(':') + 1).trim()) as SSEEvent, asstId);
          } catch { /* skip malformed chunk */ }
        }
      }
    } catch { /* aborted or connection dropped */ }

    flushText(asstId);
    // If the stream ended without a terminal `done`/`error` event (both clear
    // `streaming`) and the user didn't stop it, the connection died abnormally —
    // surface it rather than rendering a silent, successful-looking stop.
    const stillStreaming = messagesRef.current.find((m) => m.id === asstId)?.streaming;
    if (stillStreaming && !userAbortedRef.current) {
      updateMessage(asstId, (m) => ({
        ...m,
        content: (m.content ? m.content + '\n\n' : '') + '⚠️ The connection ended unexpectedly.',
        streaming: false,
        error: true,
      }));
    } else {
      updateMessage(asstId, (m) => (m.streaming ? { ...m, streaming: false } : m));
    }
    setIsStreaming(false);
    abortRef.current = null;
  }, [flushText, handleEvent, updateMessage]);

  const startAssistant = useCallback((extra: ChatMessage[]) => {
    const asstId = newId();
    userAbortedRef.current = false;
    textBufRef.current[asstId] = '';
    commit([...messagesRef.current, ...extra, { id: asstId, role: 'assistant', content: '', streaming: true }]);
    setIsStreaming(true);
    return asstId;
  }, [commit]);

  const sendMessage = useCallback((text: string, files?: File[]) => {
    if (abortRef.current) return; // a turn is already streaming
    const userMsg: ChatMessage = { id: newId(), role: 'user', content: text };
    const asstId = startAssistant([userMsg]);
    const payload = {
      messages: [{ role: 'user', content: text }],
      conversation_id: convIdRef.current,
      tool_mode: toolModeRef.current,
    };
    if (files && files.length) {
      const fd = new FormData();
      fd.append('payload', JSON.stringify(payload));
      for (const f of files) fd.append('files', f);
      void runStream(fd, true, asstId);
    } else {
      void runStream(JSON.stringify(payload), false, asstId);
    }
  }, [runStream, startAssistant]);

  const continueTurn = useCallback(() => {
    if (!convIdRef.current || abortRef.current) return;
    const asstId = startAssistant([]);
    void runStream(
      JSON.stringify({ messages: [], conversation_id: convIdRef.current, tool_mode: toolModeRef.current }),
      false,
      asstId,
    );
  }, [runStream, startAssistant]);

  const resolve = useCallback(async (msgId: string, toolUseId: string, decision: 'approve' | 'deny') => {
    const token = getToken();
    // Send the server-side row id (msgId on the pending card) so the backend
    // resolves THIS pending write even if a positional-id provider reused the
    // tool_use_id across turns.
    const card = messagesRef.current
      .find((m) => m.id === msgId)?.pendingConfirmations
      ?.find((c) => c.toolUseId === toolUseId);
    let result: unknown;
    try {
      const res = await fetch(`${API}/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({
          conversation_id: convIdRef.current,
          tool_use_id: toolUseId,
          decision,
          msg_id: card?.msgId,
        }),
      });
      if (res.status === 401) {
        sessionStorage.removeItem(TOKEN_KEY);
        window.location.href = '/login';
        return;
      }
      if (!res.ok) {
        // The server may have claimed/executed the write but failed to persist —
        // do NOT mark the card resolved or start a continuation. Leave it pending
        // so the user can retry (the resolve is idempotent server-side).
        toast.error('Could not complete that action. Please try again.');
        return;
      }
      const body = await res.json();
      result = body?.result;
    } catch { /* leave the card pending; the user can retry */ return; }

    updateMessage(msgId, (m) => ({
      ...m,
      pendingConfirmations: (m.pendingConfirmations ?? []).map((c) =>
        c.toolUseId === toolUseId
          ? { ...c, status: decision === 'approve' ? 'approved' : 'denied', result }
          : c,
      ),
    }));

    const msg = messagesRef.current.find((m) => m.id === msgId);
    const allResolved = (msg?.pendingConfirmations ?? []).every((c) => c.status !== 'pending');
    if (allResolved) continueTurn();
  }, [continueTurn, updateMessage]);

  const approveAction = useCallback((msgId: string, toolUseId: string) => resolve(msgId, toolUseId, 'approve'), [resolve]);
  const denyAction = useCallback((msgId: string, toolUseId: string) => resolve(msgId, toolUseId, 'deny'), [resolve]);

  const stop = useCallback(() => {
    userAbortedRef.current = true;
    abortRef.current?.abort();
    abortRef.current = null;
    setIsStreaming(false);
  }, []);

  const clear = useCallback(() => {
    convIdRef.current = null;
    setConversationId(null);
    setContextUsage(null);
    commit([]);
  }, [commit]);

  const loadMessages = useCallback((serverMsgs: ServerMessage[], convId: string) => {
    const isPending = (r: unknown): boolean =>
      !!r && typeof r === 'object' && (r as { status?: string }).status === 'pending_user_approval';
    const mapped: ChatMessage[] = serverMsgs.map((sm) => {
      const calls = sm.tool_calls ?? [];
      return {
        id: sm.id,
        role: sm.role,
        content: sm.content,
        model: sm.model,
        // A still-pending write reloads as a re-approvable confirmation card.
        pendingConfirmations: calls
          .filter((tc) => isPending(tc.result))
          .map((tc) => ({
            tool: tc.tool,
            toolUseId: tc.tool_use_id,
            msgId: sm.id,
            args: tc.args ?? {},
            status: 'pending' as const,
          })),
        toolCalls: calls
          .filter((tc) => !isPending(tc.result))
          .map((tc) => ({
            tool: tc.tool,
            toolUseId: tc.tool_use_id,
            args: tc.args,
            result: tc.result,
            status: 'done' as const,
          })),
      };
    });
    convIdRef.current = convId;
    setConversationId(convId);
    commit(mapped);
  }, [commit]);

  const changeToolMode = useCallback((mode: ToolMode) => {
    toolModeRef.current = mode;
    setToolMode(mode);
  }, []);

  return {
    messages,
    isStreaming,
    conversationId,
    toolMode,
    setToolMode: changeToolMode,
    contextUsage,
    sendMessage,
    approveAction,
    denyAction,
    stop,
    clear,
    loadMessages,
  };
}
