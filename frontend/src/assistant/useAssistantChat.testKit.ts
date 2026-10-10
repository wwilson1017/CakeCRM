// Shared by the #282 hook suites: a hand-driven SSE response and the Probe mount.
// Not a test file — each suite declares its own `vi.mock` of the api client, which is
// hoisted per file and covers the hook imported from here.

import { act, createElement, useEffect } from 'react';
import type { Root } from 'react-dom/client';
import { vi } from 'vitest';

import { useAssistantChat } from './useAssistantChat';

export type Chat = ReturnType<typeof useAssistantChat>;

const SSE = { status: 200, headers: { 'Content-Type': 'text/event-stream' } };
const frame = (e: object) => new TextEncoder().encode(`data: ${JSON.stringify(e)}\n\n`);

/** An event-stream response the test drives: push frames, then close it or drop it. A real
 *  fetch tears its body down when the signal aborts; a hand-built Response needs it wired. */
export function openStream(init?: RequestInit) {
  let ctl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({ start(c) { ctl = c; } });
  let over = false;
  const fail = (err: Error) => { if (!over) { over = true; ctl.error(err); } };
  init?.signal?.addEventListener('abort', () => {
    const err = new Error('aborted');
    err.name = 'AbortError';
    fail(err);
  });
  return {
    response: new Response(body, SSE),
    aborted: () => !!init?.signal?.aborted,
    push: (...events: object[]) => { if (!over) events.forEach((e) => ctl.enqueue(frame(e))); },
    close: () => { if (!over) { over = true; ctl.close(); } },
    drop: () => fail(new Error('network went away')),
  };
}
export type Stream = ReturnType<typeof openStream>;

/** Frames, then the server closes the stream. */
export function sseResponse(events: object[]): Response {
  const s = openStream();
  s.push(...events);
  s.close();
  return s.response;
}

export function mountChat(root: Root): { current: () => Chat } {
  let latest: Chat;
  function Probe() {
    const chat = useAssistantChat(null, null);
    useEffect(() => { latest = chat; });
    return null;
  }
  act(() => { root.render(createElement(Probe)); });
  return { current: () => latest! };
}

/** Let the stream reader, React and any timer due within `ms` run. */
export async function advance(ms = 0) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

/** `document.visibilityState`, settable. jsdom's is a constant 'visible'. */
export function stubVisibility(initial: DocumentVisibilityState) {
  let state = initial;
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state });
  return {
    async set(next: DocumentVisibilityState) {
      state = next;
      await act(async () => {
        document.dispatchEvent(new Event('visibilitychange'));
        await vi.advanceTimersByTimeAsync(0);
      });
    },
    restore() { Reflect.deleteProperty(document, 'visibilityState'); },
  };
}
