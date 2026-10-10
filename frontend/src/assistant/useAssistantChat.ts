// CakeCRM — assistant streaming chat hook.
//
// Consumes the /api/assistant SSE stream over a POST + fetch ReadableStream
// (get_current_user reads a Bearer header, so a browser EventSource can't be
// used). Text deltas are rAF-batched (a 50 ms timer while the tab is hidden, which gets
// no animation frames); tool/confirm events update the in-flight assistant message.
// Write confirmations are server-authoritative: approve/deny POST /confirm, and once a
// message's last pending card resolves we re-POST an empty-messages continuation so the
// model finishes the turn.
//
// Detached turns (#282). The server runs a turn as a task it owns and logs every frame
// with a `seq` (backend/assistant/turns.py), so this hook mints the `turn_id`, dedupes
// frames by `seq`, and when a stream ends without a terminal frame — or goes silent 45 s
// after `turn_start`, or the server closes it with a `reattach` frame — reopens
// `GET /turns/{id}/events?after=<last seq>` (`reattachLoop`): at once, then 0.5 s doubling
// to 8 s, no attempt budget, parked while the tab is hidden or offline. The "connection
// ended" note is the fallback taken ONLY when the server does not know the turn: a 4xx
// (408/429 retry) or a 200 that is not `text/event-stream`. Stop = cancel + follow the
// turn to its terminal frame (10 s bound) + one conversation read. New chat, switching
// conversations and unmount DETACH: the server keeps working, and reopening the thread
// finds it as `running_turn` and attaches. Lifecycle rule: one `ActiveTurn` per turn;
// every loop checks `isLive(turn)` after each await before touching state, and
// `release(turn)` is a no-op unless that turn is still the active one.

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { api } from '../core/api/client';
import { getToken, TOKEN_KEY } from '../core/auth/tokenUtils';
import { parseUTC } from '../crm/gtd/util';
import { toast } from '../shared/toast';
import type {
  ActiveRecordContext,
  ChatMessage,
  ContextUsage,
  ServerMessage,
  PageContext,
  RunningTurn,
  ToolCallInfo,
  ToolMode,
  WorkingState,
} from './types';

const API = '/api/assistant';

const DROP_NOTE = '⚠️ The connection ended unexpectedly.';
const REATTACH_FIRST_DELAY_MS = 500;
const REATTACH_MAX_DELAY_MS = 8_000;
/** Silence on a stream the server acknowledged (`turn_start`). It pings every 15 s, so
 *  a false fire only reattaches. An unacknowledged stream keeps no idle timeout. */
const ACK_IDLE_TIMEOUT_MS = 45_000;
/** Coming back to the foreground aborts an acknowledged stream silent this long: a
 *  suspended socket often never errors. */
const FOREGROUND_STALE_MS = 20_000;
const STOP_FOLLOW_MS = 10_000;

/** What one turn was started against: the CRM record open behind the drawer and the
 *  settings section on screen. Snapshotted per assistant message so a post-confirmation
 *  continuation resumes the turn as it started, even if the user has navigated since.
 *  Either half may be absent; `undefined` for a key means "send no such field". */
interface TurnContext {
  context?: { record_type: string; record_id: number };
  page?: PageContext;
}

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

/** Server rows → chat messages: a still-pending write reloads as a re-approvable card. */
function mapServerMessages(serverMsgs: ServerMessage[]): ChatMessage[] {
  const isPending = (r: unknown): boolean =>
    !!r && typeof r === 'object' && (r as { status?: string }).status === 'pending_user_approval';
  return serverMsgs.map((sm) => {
    const calls = sm.tool_calls ?? [];
    return {
      id: sm.id,
      role: sm.role,
      content: sm.content,
      model: sm.model,
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
}

// ── The turn this hook is showing (#282) ─────────────────────────────────────

interface ActiveTurn {
  turnId: string;
  /** The assistant bubble the turn streams into. */
  asstId: string;
  /** Highest frame seq applied; a reattach asks for what comes after it. */
  lastSeq: number;
  sawTerminal: boolean;
  /** The server sent `turn_start`: it runs turns detached and can be reattached to. */
  sawTurnStart: boolean;
  /** Read the conversation once at the end (it was reattached, opened running, or
   *  stopped), so the panel shows exactly what the server recorded. */
  reconcile: boolean;
  /** Opened from history while running: a server that no longer knows it means it just
   *  ended, so read the conversation instead of showing a drop note. */
  attachedOnLoad: boolean;
  startedAt: number;
  phase: 'model' | 'writing' | 'tool';
  tool?: string;
  /** Detached, switched away from or replaced: this hook touches no more state for it. */
  ended: boolean;
  /** Stop was pressed: following the turn to its terminal frame. */
  stopped: boolean;
  /** When a reattach loop gives up on its own (Stop's bounded follow); null = never. */
  deadline: number | null;
  /** The fetch in flight right now. The idle watchdog aborts only this. */
  ctl: AbortController | null;
  /** When the open stream last produced bytes; null while no stream is open. */
  quietSince: number | null;
  /** Ends a `pause` early. */
  wake: (() => void) | null;
}

/** A v4 UUID. `crypto.randomUUID` exists only in a secure context, and the app is also
 *  served over plain HTTP on a LAN address; `getRandomValues` works in both. */
function mintTurnId(): string {
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

function newTurn(turnId: string, asstId: string): ActiveTurn {
  return {
    turnId, asstId, lastSeq: -1, sawTerminal: false, sawTurnStart: false, reconcile: false,
    attachedOnLoad: false, startedAt: Date.now(), phase: 'model', ended: false, stopped: false,
    deadline: null, ctl: null, quietSince: null, wake: null,
  };
}

function endTurn(turn: ActiveTurn) {
  turn.ended = true;
  turn.ctl?.abort();
  turn.wake?.();
}

/** Resolves after `ms` (never, when null), or as soon as the turn is woken. */
function pause(turn: ActiveTurn, ms: number | null): Promise<void> {
  return new Promise((resolve) => {
    const done = () => { clearTimeout(timer); turn.wake = null; resolve(); };
    const timer = ms === null ? undefined : setTimeout(done, ms);
    turn.wake = done;
  });
}

/** One fetch's lifetime: its own AbortController and the idle watchdog that aborts only it. */
function fetchScope(turn: ActiveTurn) {
  const ctl = new AbortController();
  turn.ctl = ctl;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const scope = {
    signal: ctl.signal,
    // ANY bytes reset it, so a keepalive ping counts as proof of life.
    arm: () => {
      clearTimeout(timer);
      turn.quietSince = Date.now();
      if (turn.sawTurnStart) timer = setTimeout(() => ctl.abort(), ACK_IDLE_TIMEOUT_MS);
    },
    close: () => { clearTimeout(timer); turn.quietSince = null; },
  };
  // Armed from the start, so a reattach that stalls before its first byte is retried too.
  scope.arm();
  return scope;
}

const pastDeadline = (turn: ActiveTurn) => turn.deadline !== null && Date.now() >= turn.deadline;

// A hidden or offline tab cannot usefully reconnect; it waits for the page to come back.
const backgrounded = () => document.visibilityState === 'hidden' || navigator.onLine === false;

const authHeaders = (): Record<string, string> => {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
};

function logout() {
  sessionStorage.removeItem(TOKEN_KEY);
  window.location.href = '/login';
}

export function useAssistantChat(
  recordContext?: ActiveRecordContext | null,
  pageContext?: PageContext | null,
) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [toolMode, setToolMode] = useState<ToolMode>('normal');
  const [contextUsage, setContextUsage] = useState<ContextUsage | null>(null);
  // What the in-flight turn is doing (#282); null until `turn_start`, so a stream the
  // server never acknowledged keeps the plain "…" placeholder.
  const [working, setWorking] = useState<WorkingState | null>(null);

  const messagesRef = useRef<ChatMessage[]>([]);
  const convIdRef = useRef<string | null>(null);
  const toolModeRef = useRef<ToolMode>('normal');
  const activeTurnRef = useRef<ActiveTurn | null>(null);
  const textBufRef = useRef<Record<string, string>>({});
  const rafRef = useRef<number | null>(null);
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Latest-value ref for the open CRM record, mirrored in the COMMIT phase
  // (useLayoutEffect runs after commit, before paint and before any event handler
  // can fire), so a send/continuation fired from a click always reads the record
  // currently on screen — with no render-phase side effect and no committed-render
  // window. (convIdRef/toolModeRef are assigned imperatively at their own call
  // sites; this ref tracks a prop, hence the layout-effect mirror.)
  const recordCtxRef = useRef<ActiveRecordContext | null>(null);
  useLayoutEffect(() => { recordCtxRef.current = recordContext ?? null; }, [recordContext]);
  // The settings section open behind the drawer (issue #200), mirrored in the commit
  // phase for exactly the same reason as the record above: a chip click must read the
  // section currently on screen, not the one a passive effect had not caught up to.
  const pageCtxRef = useRef<PageContext | null>(null);
  useLayoutEffect(() => { pageCtxRef.current = pageContext ?? null; }, [pageContext]);

  // Per-assistant-message context snapshot, keyed by the client message id. A
  // confirmation belongs to a specific assistant message; its post-confirm
  // continuation must reuse the record open when THAT message's turn started — not a
  // global "last turn" value, because the user can send another message on a different
  // record before approving an earlier confirmation. A reload-resumed conversation has
  // no snapshot for its restored message id → context is OMITTED (never the live
  // record), so a reload can't rebind the resumed turn either.
  // Both contexts travel together in one snapshot (#200): a continuation has to resume
  // the turn as it started, and "which settings section was open" is as much a part of
  // that as "which record" — the user can navigate between proposing a write and
  // approving it.
  const turnCtxByMsgRef = useRef<Record<string, TurnContext | undefined>>({});

  // Only type + id cross the wire — label is display-only (injection boundary).
  const wireTurn = useCallback((): TurnContext => {
    const ctx = recordCtxRef.current;
    const page = pageCtxRef.current;
    return {
      context: ctx ? { record_type: ctx.recordType, record_id: ctx.recordId } : undefined,
      // Ids are re-sent as-is because they are already closed sets on both sides; the
      // backend re-validates them as Literals regardless. Rebuilt field by field so no
      // stray key ever rides along.
      page: !page ? undefined
        : page.page === 'settings' ? { page: page.page, section: page.section }
          : { page: page.page },
    };
  }, []);

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
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    if (flushTimerRef.current != null) clearTimeout(flushTimerRef.current);
    rafRef.current = null;
    flushTimerRef.current = null;
    const buf = textBufRef.current[id];
    if (buf != null) updateMessage(id, (m) => ({ ...m, content: buf }));
  }, [updateMessage]);

  const scheduleFlush = useCallback((id: string) => {
    if (rafRef.current != null || flushTimerRef.current != null) return;
    // A hidden document gets no animation frames, so a backgrounded answer used to pile
    // up here and paint all at once on return (#282).
    if (document.visibilityState === 'hidden') {
      flushTimerRef.current = setTimeout(() => flushText(id), 50);
    } else {
      rafRef.current = requestAnimationFrame(() => flushText(id));
    }
  }, [flushText]);

  const isLive = useCallback((turn: ActiveTurn) => !turn.ended && activeTurnRef.current === turn, []);

  // The turn is over for this hook: hand `isStreaming` back — unless a newer turn owns it.
  const release = useCallback((turn: ActiveTurn) => {
    if (activeTurnRef.current !== turn) return;
    activeTurnRef.current = null;
    setIsStreaming(false);
    setWorking(null);
  }, []);

  const showWorking = useCallback((turn: ActiveTurn, phase?: WorkingState['phase']) => {
    setWorking({ phase: phase ?? turn.phase, tool: turn.tool, startedAt: turn.startedAt, messageId: turn.asstId });
  }, []);

  const showPhase = useCallback((turn: ActiveTurn, phase: ActiveTurn['phase'], tool?: string) => {
    if (turn.stopped) return; // it says "Stopping…" until the server confirms
    turn.phase = phase;
    turn.tool = tool;
    // Null until `turn_start`: a stream the server never acknowledged shows no clock.
    setWorking((w) => (w ? { ...w, phase, tool } : w));
  }, []);

  const appendNote = useCallback((turn: ActiveTurn, note: string) => {
    flushText(turn.asstId);
    updateMessage(turn.asstId, (m) => ({
      ...m, content: (m.content ? m.content + '\n\n' : '') + note, streaming: false, error: true,
    }));
  }, [flushText, updateMessage]);

  const handleEvent = useCallback((evt: SSEEvent, turn: ActiveTurn) => {
    // Every frame of a detached turn carries a seq; a replayed one is already applied.
    if (typeof evt.seq === 'number') {
      if (evt.seq <= turn.lastSeq) return;
      turn.lastSeq = evt.seq;
    }
    const asstId = turn.asstId;
    switch (evt.type) {
      case 'turn_start': {
        turn.sawTurnStart = true;
        turn.startedAt = parseUTC(String(evt.started_at ?? '')).getTime() || turn.startedAt;
        if (!turn.stopped) showWorking(turn);
        break;
      }
      case 'conversation_id': {
        const id = String(evt.id ?? '');
        convIdRef.current = id;
        setConversationId(id);
        break;
      }
      case 'text':
        showPhase(turn, 'writing');
        textBufRef.current[asstId] = (textBufRef.current[asstId] ?? '') + String(evt.text ?? '');
        scheduleFlush(asstId);
        break;
      case 'tool_start':
        showPhase(turn, 'tool', String(evt.tool ?? ''));
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
        showPhase(turn, 'model');
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
        turn.sawTerminal = true;
        // Stopped here or elsewhere: show what the server recorded once it is over.
        if (evt.stopped) turn.reconcile = true;
        flushText(asstId);
        updateMessage(asstId, (m) => ({ ...m, model: evt.model ? String(evt.model) : m.model, streaming: false }));
        break;
      case 'error': {
        turn.sawTerminal = true;
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
        break; // `ping`: its bytes already re-armed the idle watchdog
    }
  }, [flushText, scheduleFlush, showPhase, showWorking, updateMessage]);

  /** Read one SSE response to its end. Throws on a transport failure or an abort. */
  const readStream = useCallback(async (
    res: Response, turn: ActiveTurn, arm: () => void,
  ): Promise<'terminal' | 'reattach' | 'closed'> => {
    const reader = res.body?.getReader();
    if (!reader) throw new Error('No response body');
    const decoder = new TextDecoder();
    let buffer = '';
    try {
      for (;;) {
        const { done, value } = await reader.read();
        // A turn that was stopped, switched away from or replaced touches no more state.
        if (done || !isLive(turn)) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() ?? '';
        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith('data:')) continue;
          let evt: SSEEvent;
          try {
            evt = JSON.parse(line.slice(line.indexOf(':') + 1).trim()) as SSEEvent;
          } catch { continue; /* skip malformed chunk */ }
          if (evt.type === 'reattach') {
            // The server is closing this stream at its time cap, not the turn.
            turn.lastSeq = Math.max(turn.lastSeq, Number(evt.after ?? -1));
            return 'reattach';
          }
          handleEvent(evt, turn);
        }
        // After the frames, so the one carrying `turn_start` already arms the short window.
        arm();
      }
    } finally {
      reader.cancel().catch(() => {});
    }
    return turn.sawTerminal ? 'terminal' : 'closed';
  }, [handleEvent, isLive]);

  // Follow a turn whose stream dropped (or that was running when the thread was opened)
  // through the server's log: frames after the last seq replayed, then live.
  // 'gone' = the server does not know the turn (or Stop's follow ran out of time).
  const reattachLoop = useCallback(async (turn: ActiveTurn): Promise<'terminal' | 'gone' | 'ended'> => {
    for (let attempt = 0; ; attempt++) {
      if (!isLive(turn)) return 'ended';
      if (pastDeadline(turn)) return 'gone';
      // Not before the first try on a turn the server never acknowledged: a server that
      // does not know it answers at once, and a one-frame "Reconnecting…" would flicker.
      if (!turn.stopped && (turn.sawTurnStart || attempt > 0)) showWorking(turn, 'reconnecting');
      if (attempt > 0) await pause(turn, Math.min(REATTACH_FIRST_DELAY_MS * 2 ** (attempt - 1), REATTACH_MAX_DELAY_MS));
      while (isLive(turn) && backgrounded() && !pastDeadline(turn)) await pause(turn, null);
      if (!isLive(turn)) return 'ended';
      if (pastDeadline(turn)) return 'gone';

      const before = turn.lastSeq;
      const scope = fetchScope(turn);
      try {
        const res = await fetch(`${API}/turns/${turn.turnId}/events?after=${turn.lastSeq}`, {
          headers: authHeaders(), signal: scope.signal,
        });
        if (res.status === 401) {
          if (isLive(turn)) logout();
          return 'ended';
        }
        // A missing turn is a 404. Anything that is not an event stream — the SPA
        // catch-all's index.html during a rolling deploy — means the same thing.
        const isStream = res.ok && (res.headers.get('content-type') ?? '').includes('text/event-stream');
        const retriable = res.status >= 500 || res.status === 408 || res.status === 429;
        if (!isStream && !retriable) return 'gone';
        if (!isStream) continue;
        turn.reconcile = true;
        if (!turn.stopped) showWorking(turn);
        if ((await readStream(res, turn, scope.arm)) === 'terminal') return 'terminal';
      } catch {
        // A transport failure, or the idle watchdog's abort: try again.
      } finally {
        scope.close();
        if (turn.lastSeq > before) attempt = -1; // it made progress — reconnect at once
      }
    }
  }, [isLive, readStream, showWorking]);

  // One read of the conversation, replacing the panel with what the server recorded —
  // committed only if this turn is still the one on screen.
  const reconcileConversation = useCallback(async (turn: ActiveTurn) => {
    const convId = convIdRef.current;
    if (!convId) return false;
    try {
      const conv = await api<{ messages: ServerMessage[] }>(`${API}/conversations/${convId}`);
      if (!isLive(turn) || convIdRef.current !== convId) return true;
      textBufRef.current = {};
      commit(mapServerMessages(conv.messages));
      return true;
    } catch {
      return false; // keep what streamed
    }
  }, [commit, isLive]);

  // What is left of a turn once its first stream ended without a terminal frame.
  const settle = useCallback(async (turn: ActiveTurn, reachedTerminal: boolean) => {
    const outcome = reachedTerminal ? 'terminal' : await reattachLoop(turn);
    if (outcome === 'ended' || !isLive(turn)) return;
    flushText(turn.asstId);
    if (outcome === 'gone' && !(turn.attachedOnLoad && await reconcileConversation(turn))) {
      if (isLive(turn)) appendNote(turn, DROP_NOTE);
    } else if (outcome === 'terminal' && turn.reconcile) {
      await reconcileConversation(turn);
    }
    if (!isLive(turn)) return;
    updateMessage(turn.asstId, (m) => (m.streaming ? { ...m, streaming: false } : m));
    release(turn);
  }, [appendNote, flushText, isLive, reattachLoop, reconcileConversation, release, updateMessage]);

  const runStream = useCallback(async (body: BodyInit, isForm: boolean, turn: ActiveTurn) => {
    const headers: Record<string, string> = authHeaders();
    if (!isForm) headers['Content-Type'] = 'application/json';
    const endpoint = isForm ? `${API}/chat/upload` : `${API}/chat`;
    const scope = fetchScope(turn);
    let end: 'terminal' | 'reattach' | 'closed' = 'closed';
    try {
      const res = await fetch(endpoint, { method: 'POST', headers, body, signal: scope.signal });
      if (!isLive(turn)) return;
      if (res.status === 401) {
        // Only the active turn drives the logout; a superseded one just stops quietly.
        logout();
        release(turn);
        return;
      }
      if (!res.ok || !res.body) {
        // Refused before any turn started (no provider, bad payload): nothing to follow.
        let detail = 'Request failed.';
        try {
          const b = await res.json();
          if (b?.detail) detail = String(b.detail);
        } catch { /* not JSON */ }
        updateMessage(turn.asstId, (m) => ({ ...m, content: `⚠️ ${detail}`, streaming: false, error: true }));
        release(turn);
        return;
      }
      end = await readStream(res, turn, scope.arm);
    } catch {
      /* transport failure, the idle watchdog, or a detach/Stop abort */
    } finally {
      scope.close();
    }
    if (!isLive(turn)) return; // detached or stopped: whoever ended it owns the bubble now
    await settle(turn, end === 'terminal');
  }, [isLive, readStream, release, settle, updateMessage]);

  const startAssistant = useCallback((extra: ChatMessage[]) => {
    const asstId = newId();
    const turn = newTurn(mintTurnId(), asstId);
    activeTurnRef.current = turn;
    textBufRef.current[asstId] = '';
    commit([...messagesRef.current, ...extra, { id: asstId, role: 'assistant', content: '', streaming: true }]);
    setIsStreaming(true);
    return turn;
  }, [commit]);

  const sendMessage = useCallback((text: string, files?: File[]) => {
    if (activeTurnRef.current) return; // a turn is already streaming
    const userMsg: ChatMessage = { id: newId(), role: 'user', content: text };
    const turn = startAssistant([userMsg]);
    // Snapshot the record for THIS message's turn so its post-confirm continuation
    // reuses it (keyed by the assistant message id).
    const ctx = wireTurn();
    turnCtxByMsgRef.current[turn.asstId] = ctx;
    const payload = {
      messages: [{ role: 'user', content: text }],
      conversation_id: convIdRef.current,
      tool_mode: toolModeRef.current,
      // JSON.stringify drops an `undefined` value, so no key is added when no
      // record is open — the wire shape stays back-compatible. Same for `page`.
      context: ctx.context,
      page: ctx.page,
      turn_id: turn.turnId,
    };
    if (files && files.length) {
      const fd = new FormData();
      fd.append('payload', JSON.stringify(payload));
      // Its own form field: the upload route reads it beside `payload`, not inside it.
      fd.append('turn_id', turn.turnId);
      for (const f of files) fd.append('files', f);
      void runStream(fd, true, turn);
    } else {
      void runStream(JSON.stringify(payload), false, turn);
    }
  }, [runStream, startAssistant, wireTurn]);

  const continueTurn = useCallback((ctx: TurnContext | undefined) => {
    if (!convIdRef.current || activeTurnRef.current) return;
    const turn = startAssistant([]);
    // Carry the snapshot from the message being resumed, and propagate it forward: if
    // this continuation itself proposes a write, its own continuation reuses the same
    // record and page. (undefined = nothing open, or an unknowable reload-resumed turn.)
    turnCtxByMsgRef.current[turn.asstId] = ctx;
    void runStream(
      JSON.stringify({
        messages: [],
        conversation_id: convIdRef.current,
        tool_mode: toolModeRef.current,
        context: ctx?.context,
        page: ctx?.page,
        turn_id: turn.turnId,
      }),
      false,
      turn,
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
      // Derive the card's TRUE status from the server's canonical outcome, not
      // from our own click — the response may report already_resolved (an
      // approve/deny race the other click won) or still-executing.
      const rstatus =
        result && typeof result === 'object' ? (result as { status?: string }).status : undefined;
      const resultErrored =
        result && typeof result === 'object' && (result as { error?: unknown }).error != null;
      let cardStatus: 'approved' | 'denied' | 'pending' | 'failed';
      if (rstatus === 'executing') {
        cardStatus = 'pending'; // resolved elsewhere but not yet finalized — stay pending
      } else if (rstatus === 'denied_by_user') {
        cardStatus = 'denied';
      } else if (resultErrored) {
        // The canonical outcome is an executor error (e.g. Gmail disconnected) —
        // show it as failed, not "Approved" (issue #8). Independent of this
        // request's decision: a deny that lost a race to an already-resolved
        // errored approve returns that same canonical error via `already_resolved`,
        // and a genuine deny never yields an error result (it returns a denied
        // status, handled above), so this can't mislabel a real denial.
        cardStatus = 'failed';
      } else if (body?.status === 'already_resolved') {
        cardStatus = 'approved'; // resolved by a prior action and not a denial
      } else {
        cardStatus = decision === 'approve' ? 'approved' : 'denied';
      }

      updateMessage(msgId, (m) => ({
        ...m,
        pendingConfirmations: (m.pendingConfirmations ?? []).map((c) =>
          c.toolUseId === toolUseId ? { ...c, status: cardStatus, result } : c,
        ),
      }));

      // Continue only once EVERY card on this message reached a final state. A
      // 'failed' card (an approved write whose executor errored, e.g. Gmail
      // disconnected — issue #8) is final too, so the turn continues and the model
      // can react to the error; hence `!== 'pending'` rather than an approved/denied
      // allow-list.
      const msg = messagesRef.current.find((m) => m.id === msgId);
      // If the message vanished (e.g. the user switched conversations while /confirm
      // was in flight), do NOT continue: an empty pendingConfirmations list makes
      // .every() vacuously true, which would resume this turn against the CURRENT
      // conversation — the wrong thread.
      if (!msg) return;
      // 'failed' is terminal too (issue #8): a write whose executor errored must not
      // wedge the turn waiting for a status that will never change.
      const allFinal = (msg.pendingConfirmations ?? []).every((c) => c.status !== 'pending');
      // Resume with the snapshot captured when THIS message's turn started (undefined
      // for a reload-resumed message that has no snapshot → context omitted).
      if (allFinal) continueTurn(turnCtxByMsgRef.current[msgId]);
    } catch { /* leave the card pending; the user can retry */ }
  }, [continueTurn, updateMessage]);

  const approveAction = useCallback((msgId: string, toolUseId: string) => resolve(msgId, toolUseId, 'approve'), [resolve]);
  const denyAction = useCallback((msgId: string, toolUseId: string) => resolve(msgId, toolUseId, 'deny'), [resolve]);

  // Stop means stop the TURN (#282): the server keeps running a turn whose browser merely
  // went away, so tell it — then follow the turn to its terminal frame (bounded) and show
  // what it recorded. This view of the turn ends; a copy takes its place as the active
  // turn, so `isStreaming` holds until the server's rows are on screen and a second press
  // is a no-op.
  const stop = useCallback(() => {
    const turn = activeTurnRef.current;
    if (!turn || turn.stopped) return;
    endTurn(turn);
    flushText(turn.asstId);
    const after: ActiveTurn = {
      ...turn, ended: false, stopped: true, ctl: null, wake: null, quietSince: null,
      deadline: Date.now() + STOP_FOLLOW_MS,
    };
    activeTurnRef.current = after;
    showWorking(after, 'stopping');
    // One timer bounds the whole follow: it aborts whatever is in flight and wakes any
    // pause (a backoff, or a hidden/offline park).
    const giveUp = setTimeout(() => { after.ctl?.abort(); after.wake?.(); }, STOP_FOLLOW_MS);
    void (async () => {
      // The POST that started the turn may not have committed its row yet (a 404), and one
      // failed request must not leave the turn running: ask about once a second.
      while (isLive(after) && !pastDeadline(after)) {
        const ctl = new AbortController();
        after.ctl = ctl;
        try {
          const res = await fetch(`${API}/turns/${after.turnId}/cancel`, {
            method: 'POST', headers: authHeaders(), signal: ctl.signal,
          });
          if (res.ok) break;
        } catch { /* retry */ }
        if (isLive(after) && !pastDeadline(after)) await pause(after, 1000);
      }
      if (isLive(after)) await reattachLoop(after);
      clearTimeout(giveUp);
      if (!isLive(after)) return;
      flushText(after.asstId);
      if (!(await reconcileConversation(after)) && isLive(after)) {
        // Nothing to ask (stopped before the conversation existed) or the read failed:
        // keep what streamed, and never leave an empty bubble's dots behind.
        const next = messagesRef.current
          .map((m) => (m.id === after.asstId ? { ...m, streaming: false } : m))
          .filter((m) => m.id !== after.asstId || m.content || m.toolCalls?.length || m.pendingConfirmations?.length);
        commit(next);
      }
      release(after);
    })();
  }, [commit, flushText, isLive, reattachLoop, reconcileConversation, release, showWorking]);

  // Let go of the active turn WITHOUT stopping it: switching or clearing the conversation
  // must not kill work the server is still doing. Reopening the thread reattaches.
  const detach = useCallback(() => {
    const turn = activeTurnRef.current;
    if (!turn) return;
    endTurn(turn);
    flushText(turn.asstId);
    updateMessage(turn.asstId, (m) => (m.streaming ? { ...m, streaming: false } : m));
    release(turn);
  }, [flushText, release, updateMessage]);

  // Page lifecycle: a hidden tab parks the reattach loop; coming back wakes it, flushes
  // text that arrived meanwhile, and abandons a stream that went quiet while suspended.
  // Unmount detaches — it never cancels.
  useEffect(() => {
    const onWake = () => {
      const turn = activeTurnRef.current;
      if (!turn) return;
      flushText(turn.asstId);
      turn.wake?.();
      if (turn.sawTurnStart && turn.quietSince !== null && !backgrounded()
          && Date.now() - turn.quietSince > FOREGROUND_STALE_MS) {
        turn.ctl?.abort();
      }
    };
    document.addEventListener('visibilitychange', onWake);
    window.addEventListener('pageshow', onWake);
    window.addEventListener('online', onWake);
    return () => {
      document.removeEventListener('visibilitychange', onWake);
      window.removeEventListener('pageshow', onWake);
      window.removeEventListener('online', onWake);
      const turn = activeTurnRef.current;
      if (turn) endTurn(turn);
      activeTurnRef.current = null;
    };
  }, [flushText]);

  const clear = useCallback(() => {
    // Detach, not abort: the turn (an Auto-mode write included) carries on server-side and
    // is found again as the thread's `running_turn` from the history list.
    detach();
    convIdRef.current = null;
    turnCtxByMsgRef.current = {}; // discard all per-message context snapshots
    textBufRef.current = {};
    setConversationId(null);
    setContextUsage(null);
    commit([]);
  }, [commit, detach]);

  const loadMessages = useCallback((
    serverMsgs: ServerMessage[], convId: string, runningTurn?: RunningTurn | null,
  ) => {
    // Detach from any in-flight turn before switching — it keeps running server-side.
    detach();
    convIdRef.current = convId;
    turnCtxByMsgRef.current = {}; // a reloaded conversation has no per-message snapshots
    textBufRef.current = {};
    setConversationId(convId);
    let mapped = mapServerMessages(serverMsgs);
    if (!runningTurn) {
      commit(mapped);
      return;
    }
    // The thread has a turn running server-side: show it live, replayed from its first
    // frame. Its already-saved assistant rows would duplicate the replay, so they are
    // hidden by time (every row of the turn is saved after its row was started); this is
    // display only — the turn ends with one conversation read that replaces it all.
    const startedAt = parseUTC(runningTurn.started_at).getTime();
    const ofTurn = new Set(serverMsgs
      .filter((sm) => sm.role === 'assistant' && sm.created_at && parseUTC(sm.created_at).getTime() >= startedAt)
      .map((sm) => sm.id));
    mapped = mapped.filter((m) => !ofTurn.has(m.id));
    const asstId = newId();
    const turn = newTurn(runningTurn.turn_id, asstId);
    turn.sawTurnStart = true;
    turn.attachedOnLoad = true;
    turn.reconcile = true;
    turn.startedAt = startedAt || turn.startedAt;
    textBufRef.current[asstId] = '';
    commit([...mapped, { id: asstId, role: 'assistant', content: '', streaming: true }]);
    activeTurnRef.current = turn;
    setIsStreaming(true);
    showWorking(turn);
    void settle(turn, false);
  }, [commit, detach, settle, showWorking]);

  const changeToolMode = useCallback((mode: ToolMode) => {
    toolModeRef.current = mode;
    setToolMode(mode);
  }, []);

  return {
    messages,
    isStreaming,
    working,
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
