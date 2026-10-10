// @vitest-environment jsdom
//
// Issue #282 — a dropped stream is no longer the end of a Baker turn.
//
// The server runs a turn detached and logs its frames, so when the browser's connection
// goes (a backgrounded phone, a network change, the server's own stream cap) the hook
// reopens `GET /turns/{id}/events?after=<last seq>` and carries on where it stopped. What
// these tests pin: it resumes by seq and never doubles a frame; it has no attempt budget
// but backs off; it stays quiet while the tab cannot reach anything; the fallback note is
// taken only when the server does not know the turn; Stop tells the server, follows the
// turn to its end and shows what was recorded; switching away or unmounting lets go
// WITHOUT stopping; and opening a thread with a live turn attaches to it.

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }));
vi.mock('../core/api/client', () => ({ api: apiMock, ApiError: class ApiError extends Error {} }));

import type { ServerMessage } from './types';
import { advance, mountChat, openStream, sseResponse, stubVisibility, type Chat, type Stream } from './useAssistantChat.testKit';

let container: HTMLDivElement;
let root: Root;
let fetchMock: ReturnType<typeof vi.fn>;
let post: Stream;
let postBody: BodyInit;
let turnId: string;
/** One entry per attach GET, in order; with none left the server answers 404. */
let attaches: Array<(url: string, init: RequestInit) => Promise<Response>>;
/** One entry per cancel POST; with none left the server takes it. */
let cancels: Array<() => Promise<Response>>;
let convReads: number;
let convRead: () => Promise<{ messages: ServerMessage[] }>;
let visibility: ReturnType<typeof stubVisibility>;

const urls = () => fetchMock.mock.calls.map(([url]) => url as string);
const attachUrls = () => urls().filter((u) => u.includes('/events'));
const cancelUrls = () => urls().filter((u) => u.endsWith('/cancel'));
const assistants = (chat: { current: () => Chat }) => chat.current().messages.filter((m) => m.role === 'assistant');
const offline = () => Promise.reject(new TypeError('Failed to fetch'));
const SERVER_ROWS: ServerMessage[] = [
  { id: 'u1', role: 'user', content: 'hi' },
  { id: 'reply', role: 'assistant', content: 'Hello world' },
];

/** Send, and stream three frames: seq 0–2, the last one text. */
async function startTurn(chat: { current: () => Chat }, files?: File[]) {
  await act(async () => { chat.current().sendMessage('hi', files); });
  post.push(
    { type: 'turn_start', turn_id: turnId, started_at: '2026-10-10T02:00:00+00:00', seq: 0 },
    { type: 'conversation_id', id: 'conv-1', seq: 1 },
    { type: 'text', text: 'Hello ', seq: 2 },
  );
  await advance(20);
}

async function drop() {
  post.drop();
  await advance();
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-10-10T02:00:05Z'));
  attaches = [];
  cancels = [];
  convReads = 0;
  convRead = () => Promise.resolve({ messages: SERVER_ROWS });
  visibility = stubVisibility('visible');
  apiMock.mockReset();
  apiMock.mockImplementation(() => { convReads += 1; return convRead(); });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  fetchMock = vi.fn((url: string, init: RequestInit) => {
    if (url.endsWith('/cancel')) {
      const next = cancels.shift();
      return next ? next() : Promise.resolve(new Response('{"cancelled":true}', { status: 200 }));
    }
    if (init.method === 'POST') {
      postBody = init.body as BodyInit;
      turnId = typeof postBody === 'string'
        ? JSON.parse(postBody).turn_id : String((postBody as FormData).get('turn_id'));
      post = openStream(init);
      return Promise.resolve(post.response);
    }
    const next = attaches.shift();
    return next ? next(url, init) : Promise.resolve(new Response(null, { status: 404 }));
  });
  vi.stubGlobal('fetch', fetchMock);
  sessionStorage.setItem('cakecrm_token', 't');
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  visibility.restore();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  sessionStorage.clear();
});

describe('sending a turn (#282)', () => {
  it('mints a turn id and sends it in the JSON body', async () => {
    const chat = mountChat(root);
    await startTurn(chat);
    expect(turnId).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });

  it('mints the id without randomUUID, which a plain-HTTP LAN origin does not have', async () => {
    vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) });
    const chat = mountChat(root);
    await startTurn(chat);
    expect(turnId).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });

  it('sends it as its own form field on an upload', async () => {
    const chat = mountChat(root);
    await startTurn(chat, [new File(['x'], 'a.txt')]);
    const fd = postBody as FormData;
    expect(fd.get('turn_id')).toBe(turnId);
    expect(JSON.parse(String(fd.get('payload'))).turn_id).toBe(turnId);
  });

  it('leaves a turn that never dropped alone: no attach, no conversation read', async () => {
    const chat = mountChat(root);
    await startTurn(chat);
    post.push({ type: 'text', text: 'world', seq: 3 }, { type: 'done', seq: 4 });
    post.close();
    await advance(20);
    expect(attachUrls()).toEqual([]);
    expect(convReads).toBe(0);
    expect(assistants(chat).map((m) => m.content)).toEqual(['Hello world']);
    expect(chat.current().isStreaming).toBe(false);
  });

  it('the working state follows the turn and is gone at the end', async () => {
    const chat = mountChat(root);
    await act(async () => { chat.current().sendMessage('hi'); });
    expect(chat.current().working).toBeNull(); // nothing until the server acknowledges
    post.push({ type: 'turn_start', turn_id: turnId, started_at: '2026-10-10T02:00:00+00:00', seq: 0 });
    await advance(20);
    const w = chat.current().working!;
    expect([w.phase, w.startedAt, w.messageId]).toEqual(
      ['model', Date.parse('2026-10-10T02:00:00Z'), assistants(chat)[0].id]);
    post.push({ type: 'tool_start', tool: 'crm_list_deals', tool_use_id: 't1', seq: 1 });
    await advance(20);
    expect(chat.current().working).toMatchObject({ phase: 'tool', tool: 'crm_list_deals' });
    post.push({ type: 'tool_end', tool: 'crm_list_deals', tool_use_id: 't1', result: {}, seq: 2 });
    await advance(20);
    expect(chat.current().working?.phase).toBe('model');
    post.push({ type: 'text', text: 'ok', seq: 3 });
    await advance(20);
    expect(chat.current().working?.phase).toBe('writing');
    post.push({ type: 'done', seq: 4 });
    post.close();
    await advance(20);
    expect(chat.current().working).toBeNull();
  });
});

describe('reattaching to a turn whose stream dropped', () => {
  it('resumes after the last seq it saw, skips replayed frames, and ends on the server rows', async () => {
    const chat = mountChat(root);
    attaches = [() => Promise.resolve(sseResponse([
      { type: 'text', text: 'Hello ', seq: 2 }, // a replay of what already streamed
      { type: 'text', text: 'world', seq: 3 },
      { type: 'done', seq: 4 },
    ]))];
    let finishRead!: (v: { messages: ServerMessage[] }) => void;
    convRead = () => new Promise((r) => { finishRead = r; });
    await startTurn(chat);
    await drop();
    await advance(20);
    expect(attachUrls()).toEqual([`/api/assistant/turns/${turnId}/events?after=2`]);
    expect((fetchMock.mock.calls[1][1] as RequestInit).headers).toEqual({ Authorization: 'Bearer t' });
    expect(assistants(chat).map((m) => m.content)).toEqual(['Hello world']); // never doubled
    expect(chat.current().isStreaming).toBe(true); // until the server's rows are on screen
    await act(async () => { finishRead({ messages: SERVER_ROWS }); });
    expect(convReads).toBe(1);
    expect(assistants(chat).map((m) => [m.id, m.content])).toEqual([['reply', 'Hello world']]);
    expect(chat.current().isStreaming).toBe(false);
    expect(JSON.stringify(chat.current().messages)).not.toContain('⚠️');
  });

  it('reopens at once from the seq a reattach frame names', async () => {
    const chat = mountChat(root);
    attaches = [() => Promise.resolve(sseResponse([{ type: 'done', seq: 3 }]))];
    await startTurn(chat);
    post.push({ type: 'reattach', after: 2 });
    post.close();
    await advance(20);
    expect(attachUrls()).toEqual([`/api/assistant/turns/${turnId}/events?after=2`]);
    expect(chat.current().isStreaming).toBe(false);
  });

  it('keeps trying with a backoff while the server cannot be reached, adding nothing to the bubble', async () => {
    const chat = mountChat(root);
    const at: number[] = [];
    const t0 = Date.now();
    const failing = () => { at.push(Date.now() - t0); return offline(); };
    attaches = [failing, failing, failing, () => Promise.resolve(sseResponse([{ type: 'done', seq: 3 }]))];
    await startTurn(chat);
    const dropAt = Date.now() - t0;
    await drop();
    expect(chat.current().working?.phase).toBe('reconnecting');
    await advance(5000);
    expect(at.map((t) => t - dropAt)).toEqual([0, 500, 1500]);
    expect(attachUrls()).toHaveLength(4);
    expect(JSON.stringify(chat.current().messages)).not.toContain('⚠️');
    expect(chat.current().isStreaming).toBe(false);
  });

  it.each([500, 503, 429])('retries a %i after the backoff', async (status) => {
    const chat = mountChat(root);
    attaches = [
      () => Promise.resolve(new Response('no', { status })),
      () => Promise.resolve(sseResponse([{ type: 'done', seq: 3 }])),
    ];
    await startTurn(chat);
    await drop();
    await advance(600);
    expect(attachUrls()).toHaveLength(2);
    expect(JSON.stringify(chat.current().messages)).not.toContain('⚠️');
  });

  it.each([
    ['a 404', () => new Response('{"detail":"Turn not found."}', { status: 404 })],
    ['a 200 that is not an event stream', () => new Response('<!doctype html>', { status: 200, headers: { 'Content-Type': 'text/html' } })],
  ])('takes the fallback only when the server does not know the turn: %s', async (_, answer) => {
    const chat = mountChat(root);
    attaches = [() => Promise.resolve(answer())];
    await startTurn(chat);
    await drop();
    await advance(10_000);
    expect(attachUrls()).toHaveLength(1);
    const [bubble] = assistants(chat);
    expect(bubble.content).toBe('Hello \n\n⚠️ The connection ended unexpectedly.');
    expect(bubble.error).toBe(true);
    expect(chat.current().isStreaming).toBe(false);
  });

  it('abandons a stream silent for 45 s after turn_start and reattaches', async () => {
    const chat = mountChat(root);
    attaches = [() => Promise.resolve(sseResponse([{ type: 'done', seq: 3 }]))];
    await startTurn(chat);
    await advance(44_000);
    expect(attachUrls()).toEqual([]);
    await advance(1_100);
    expect(attachUrls()).toHaveLength(1);
    expect(post.aborted()).toBe(true);
  });

  it('retries a reattach that stalls before its first byte', async () => {
    const chat = mountChat(root);
    attaches = [
      (_, init) => new Promise((_resolve, reject) => {
        init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      }),
      () => Promise.resolve(sseResponse([{ type: 'done', seq: 3 }])),
    ];
    await startTurn(chat);
    await drop();
    await advance(44_000);
    expect(attachUrls()).toHaveLength(1);
    await advance(1_600);
    expect(attachUrls()).toHaveLength(2);
    expect(chat.current().isStreaming).toBe(false);
  });

  it('leaves a stream the server never acknowledged to its own devices', async () => {
    const chat = mountChat(root);
    await act(async () => { chat.current().sendMessage('hi'); });
    await advance(60_000);
    expect(attachUrls()).toEqual([]);
    expect(chat.current().isStreaming).toBe(true);
  });

  it('does not fetch from a hidden tab; it reattaches when the tab is shown', async () => {
    const chat = mountChat(root);
    attaches = [() => Promise.resolve(sseResponse([{ type: 'done', seq: 3 }]))];
    await startTurn(chat);
    await visibility.set('hidden');
    await drop();
    await advance(30_000);
    expect(attachUrls()).toEqual([]);
    await visibility.set('visible');
    await advance(20);
    expect(attachUrls()).toHaveLength(1);
    expect(chat.current().isStreaming).toBe(false);
  });

  it('waits while offline and reattaches on the online event', async () => {
    const chat = mountChat(root);
    attaches = [() => Promise.resolve(sseResponse([{ type: 'done', seq: 3 }]))];
    const onLine = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    await startTurn(chat);
    await drop();
    await advance(10_000);
    expect(attachUrls()).toEqual([]);
    onLine.mockReturnValue(true);
    await act(async () => { window.dispatchEvent(new Event('online')); });
    await advance(20);
    expect(attachUrls()).toHaveLength(1);
    onLine.mockRestore();
  });

  it('coming back to the foreground abandons a stream that went quiet while away', async () => {
    const chat = mountChat(root);
    attaches = [() => Promise.resolve(sseResponse([{ type: 'done', seq: 3 }]))];
    await startTurn(chat);
    await visibility.set('hidden');
    await advance(21_000);
    await visibility.set('visible');
    await advance(20);
    expect(post.aborted()).toBe(true);
    expect(attachUrls()).toHaveLength(1);
  });

  it('flushes streamed text in a hidden tab on a timer, not an animation frame', async () => {
    const chat = mountChat(root);
    vi.stubGlobal('requestAnimationFrame', () => 0); // a hidden tab gets no frames
    await startTurn(chat);
    await visibility.set('hidden');
    post.push({ type: 'text', text: 'world', seq: 3 });
    await advance(60);
    expect(assistants(chat)[0].content).toBe('Hello world');
  });
});

describe('Stop', () => {
  it('cancels the turn, follows it to its end, and shows what the server recorded', async () => {
    const chat = mountChat(root);
    let attach!: Stream;
    attaches = [(_, init) => { attach = openStream(init); return Promise.resolve(attach.response); }];
    await startTurn(chat);
    await act(async () => { chat.current().stop(); });
    await advance(20);
    expect(cancelUrls()).toEqual([`/api/assistant/turns/${turnId}/cancel`]);
    expect(post.aborted()).toBe(true);
    expect(chat.current().working?.phase).toBe('stopping');
    expect(chat.current().isStreaming).toBe(true); // held until the recorded rows are shown
    expect(attachUrls()).toEqual([`/api/assistant/turns/${turnId}/events?after=2`]);
    attach.push({ type: 'done', stopped: true, reason: 'you pressed Stop', seq: 3 });
    attach.close();
    await advance(20);
    expect(convReads).toBe(1);
    expect(assistants(chat).map((m) => m.id)).toEqual(['reply']);
    expect(chat.current().isStreaming).toBe(false);
    expect(chat.current().working).toBeNull();
  });

  it('asks again about once a second until the server takes the cancel', async () => {
    const chat = mountChat(root);
    cancels = [
      () => Promise.resolve(new Response(null, { status: 404 })), // the start not committed yet
      offline,
    ];
    attaches = [() => Promise.resolve(sseResponse([{ type: 'done', stopped: true, seq: 3 }]))];
    await startTurn(chat);
    await act(async () => { chat.current().stop(); });
    await advance(2_100);
    expect(cancelUrls()).toHaveLength(3);
    expect(chat.current().isStreaming).toBe(false);
  });

  it('gives the server 10 s, then keeps what streamed — with no note', async () => {
    const chat = mountChat(root);
    attaches = [(_, init) => Promise.resolve(openStream(init).response)]; // never ends
    convRead = () => Promise.reject(new Error('down'));
    await startTurn(chat);
    await act(async () => { chat.current().stop(); });
    await advance(9_000);
    expect(chat.current().isStreaming).toBe(true);
    await advance(1_100);
    expect(chat.current().isStreaming).toBe(false);
    expect(assistants(chat).map((m) => [m.content, !!m.streaming])).toEqual([['Hello ', false]]);
  });

  it('is bounded even while the tab is hidden', async () => {
    const chat = mountChat(root);
    await startTurn(chat);
    await visibility.set('hidden');
    await act(async () => { chat.current().stop(); });
    await advance(10_100);
    expect(chat.current().isStreaming).toBe(false);
  });

  it('a conversation switch during its final read wins: the stopped turn writes nothing', async () => {
    const chat = mountChat(root);
    let finishRead!: (v: { messages: ServerMessage[] }) => void;
    convRead = () => new Promise((r) => { finishRead = r; });
    attaches = [() => Promise.resolve(sseResponse([{ type: 'done', stopped: true, seq: 3 }]))];
    await startTurn(chat);
    await act(async () => { chat.current().stop(); });
    await advance(20);
    expect(convReads).toBe(1);
    act(() => { chat.current().loadMessages([{ id: 'other', role: 'user', content: 'elsewhere' }], 'conv-2'); });
    await act(async () => { finishRead({ messages: SERVER_ROWS }); });
    await advance(20);
    expect(chat.current().messages.map((m) => m.id)).toEqual(['other']);
    expect(chat.current().conversationId).toBe('conv-2');
    expect(chat.current().isStreaming).toBe(false);
  });
});

describe('letting go without stopping', () => {
  it('New chat detaches: the fetch is dropped, nothing is cancelled, and the old turn cannot write', async () => {
    const chat = mountChat(root);
    await startTurn(chat);
    const old = post;
    act(() => { chat.current().clear(); });
    await advance(20);
    expect(old.aborted()).toBe(true);
    expect(cancelUrls()).toEqual([]);
    expect(chat.current().messages).toEqual([]);
    expect(chat.current().isStreaming).toBe(false);
    old.push({ type: 'text', text: 'late', seq: 3 });
    await advance(20);
    expect(chat.current().messages).toEqual([]);
    await startTurn(chat); // a new turn starts normally
    post.push({ type: 'done', seq: 3 });
    post.close();
    await advance(20);
    expect(assistants(chat).map((m) => m.content)).toEqual(['Hello ']);
    expect(chat.current().isStreaming).toBe(false);
  });

  it('unmount lets go without cancelling, and no reattach loop outlives the component', async () => {
    const chat = mountChat(root);
    attaches = [offline, offline, offline];
    await startTurn(chat);
    await drop();
    const before = attachUrls().length;
    act(() => root.unmount());
    await advance(20_000);
    expect(attachUrls()).toHaveLength(before);
    expect(cancelUrls()).toEqual([]);
    root = createRoot(container); // afterEach unmounts again
  });
});

describe('opening a thread whose turn is still running', () => {
  const running = { turn_id: 'run-1', started_at: '2026-10-10T02:00:00+00:00', last_seq: 4 };
  const rows: ServerMessage[] = [
    { id: 'u0', role: 'user', content: 'earlier', created_at: '2026-10-10T01:00:00+00:00' },
    { id: 'a0', role: 'assistant', content: 'earlier answer', created_at: '2026-10-10T01:00:01+00:00' },
    { id: 'u1', role: 'user', content: 'hi', created_at: '2026-10-10T01:59:59.999+00:00' },
    { id: 'a1', role: 'assistant', content: 'saved mid-turn', created_at: '2026-10-10T02:00:01.123456+00:00' },
  ];

  it('attaches from the first frame, hides that turn’s saved rows, and shows it in flight', async () => {
    const chat = mountChat(root);
    let attach!: Stream;
    attaches = [(_, init) => { attach = openStream(init); return Promise.resolve(attach.response); }];
    act(() => { chat.current().loadMessages(rows, 'conv-1', running); });
    await advance(20);
    expect(attachUrls()).toEqual(['/api/assistant/turns/run-1/events?after=-1']);
    expect(chat.current().messages.map((m) => m.id).slice(0, 3)).toEqual(['u0', 'a0', 'u1']);
    expect(chat.current().messages).toHaveLength(4);
    expect(chat.current().messages[3]).toMatchObject({ role: 'assistant', content: '', streaming: true });
    expect(chat.current().isStreaming).toBe(true);
    expect(chat.current().working?.startedAt).toBe(Date.parse('2026-10-10T02:00:00Z'));
    attach.push({ type: 'turn_start', turn_id: 'run-1', started_at: running.started_at, seq: 0 },
      { type: 'text', text: 'saved mid-turn', seq: 1 }, { type: 'done', seq: 2 });
    attach.close();
    await advance(20);
    expect(convReads).toBe(1); // the server's rows replace the replay
    expect(chat.current().isStreaming).toBe(false);
  });

  it('a turn that ended in between is read back, not reported as a dropped connection', async () => {
    const chat = mountChat(root);
    act(() => { chat.current().loadMessages(rows, 'conv-1', running); }); // attach → 404
    await advance(20);
    expect(convReads).toBe(1);
    expect(JSON.stringify(chat.current().messages)).not.toContain('⚠️');
    expect(chat.current().isStreaming).toBe(false);
  });

  it('a thread with no running turn opens idle and fetches nothing', async () => {
    const chat = mountChat(root);
    act(() => { chat.current().loadMessages(rows, 'conv-1', null); });
    await advance(20);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(chat.current().messages.map((m) => m.id)).toEqual(['u0', 'a0', 'u1', 'a1']);
    expect(chat.current().isStreaming).toBe(false);
  });
});
