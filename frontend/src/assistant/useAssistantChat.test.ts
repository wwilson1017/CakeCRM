// @vitest-environment jsdom
//
// How the settings-page context (issue #200) reaches the wire, and — the part worth a test
// rather than a reading — that it obeys the SAME per-turn snapshot rule the record context
// already has.
//
// A confirmation belongs to a specific assistant message, and its continuation resumes
// that message's turn. The user can navigate between proposing a write and approving it,
// so a continuation must carry the page that was open when the turn STARTED, not the one
// on screen when they clicked Approve. Both contexts therefore travel in one snapshot;
// these cases are what stop a later change from splitting them.

import { createElement, useEffect } from 'react';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useAssistantChat } from './useAssistantChat';
import type { SettingsPageContext } from './types';

type Chat = ReturnType<typeof useAssistantChat>;

let container: HTMLDivElement;
let root: Root;
let chat: Chat;
let fetchMock: ReturnType<typeof vi.fn>;

/** One SSE response carrying the given events, then closing. */
function sse(events: Record<string, unknown>[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const evt of events) controller.enqueue(encoder.encode(`data: ${JSON.stringify(evt)}\n\n`));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

/** Publishes the hook's value after every commit. The write is in an effect rather than
 *  the render body because a render must stay side-effect free (the repo's react-hooks
 *  ruleset is `configs.recommended`), and `act` flushes effects before it resolves. */
function Probe({ page }: { page: SettingsPageContext | null }) {
  const value = useAssistantChat(null, page);
  useEffect(() => { chat = value; });
  return null;
}

function render(page: SettingsPageContext | null) {
  act(() => { root.render(createElement(Probe, { page })); });
}

/** Every JSON chat request body sent so far, parsed. */
const jsonBodies = () =>
  fetchMock.mock.calls
    .filter(([url, init]) => String(url).endsWith('/chat') && typeof init?.body === 'string')
    .map(([, init]) => JSON.parse(String(init.body)));

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  fetchMock = vi.fn(async (url: string) => {
    if (String(url).endsWith('/confirm')) {
      return new Response(JSON.stringify({ result: { status: 'ok' } }), { status: 200 });
    }
    return sse([{ type: 'conversation_id', id: 'c1' }, { type: 'done' }]);
  });
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe('the settings page context on the wire', () => {
  it('rides the JSON chat payload', async () => {
    render({ page: 'settings', section: 'integrations' });
    await act(async () => { chat.sendMessage('how do I connect Gmail?'); });

    expect(jsonBodies()[0].page).toEqual({ page: 'settings', section: 'integrations' });
  });

  it('adds no key at all when nothing is open', async () => {
    // JSON.stringify drops an undefined value, so the wire shape stays what a pre-#200
    // client sent — the same back-compat rule the record context follows.
    render(null);
    await act(async () => { chat.sendMessage('hello'); });

    expect('page' in jsonBodies()[0]).toBe(false);
    expect('context' in jsonBodies()[0]).toBe(false);
  });

  it('rides the multipart payload on the upload path too', async () => {
    render({ page: 'settings', section: 'workspace' });
    await act(async () => {
      chat.sendMessage('what is this?', [new File(['x'], 'notes.txt', { type: 'text/plain' })]);
    });

    const [url, init] = fetchMock.mock.calls.find(([u]) => String(u).endsWith('/chat/upload'))!;
    expect(String(url)).toContain('/chat/upload');
    const payload = JSON.parse(String((init!.body as FormData).get('payload')));
    expect(payload.page).toEqual({ page: 'settings', section: 'workspace' });
  });

  it('sends the section currently on screen, not the one the hook first saw', async () => {
    render({ page: 'settings', section: 'personal' });
    render({ page: 'settings', section: 'assistant' });
    await act(async () => { chat.sendMessage('what do you remember?'); });

    expect(jsonBodies()[0].page.section).toBe('assistant');
  });
});

describe('the per-turn snapshot', () => {
  it('resumes a confirmed write with the page the turn STARTED on', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith('/confirm')) {
        return new Response(JSON.stringify({ result: { status: 'ok' } }), { status: 200 });
      }
      return sse([
        { type: 'conversation_id', id: 'c1' },
        { type: 'confirm', tool: 'crm_create_contact', tool_use_id: 'tu1', msg_id: 'm1', args: {} },
        { type: 'done' },
      ]);
    });

    render({ page: 'settings', section: 'workspace' });
    await act(async () => { chat.sendMessage('add a contact'); });

    const asstId = chat.messages.find(m => m.pendingConfirmations?.length)!.id;

    // The user walks to another section before approving.
    render({ page: 'settings', section: 'integrations' });
    await act(async () => { await chat.approveAction(asstId, 'tu1'); });

    const bodies = jsonBodies();
    expect(bodies).toHaveLength(2);
    expect(bodies[1].messages).toEqual([]);              // a continuation, not a new message
    expect(bodies[1].page.section).toBe('workspace');    // the turn's own page, not the live one
  });

  it('carries no page on a continuation whose turn had none', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith('/confirm')) {
        return new Response(JSON.stringify({ result: { status: 'ok' } }), { status: 200 });
      }
      return sse([
        { type: 'conversation_id', id: 'c1' },
        { type: 'confirm', tool: 'crm_create_contact', tool_use_id: 'tu1', msg_id: 'm1', args: {} },
        { type: 'done' },
      ]);
    });

    render(null);
    await act(async () => { chat.sendMessage('add a contact'); });
    const asstId = chat.messages.find(m => m.pendingConfirmations?.length)!.id;

    // Arriving on Settings after the write was proposed must not retro-fit a page onto it.
    render({ page: 'settings', section: 'personal' });
    await act(async () => { await chat.approveAction(asstId, 'tu1'); });

    expect('page' in jsonBodies()[1]).toBe(false);
  });
});
