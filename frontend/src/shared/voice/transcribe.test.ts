// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { ApiError } from '../../core/api/client';
import { transcribeClip, transcribeRetryable } from './transcribe';
afterEach(() => vi.unstubAllGlobals());
it('posts raw audio with its explicit MIME, query flag and abort signal', async () => {
  const fetch = vi.fn<(path: string, options: RequestInit) => Promise<unknown>>(async () => ({ ok: true, status: 200, json: async () => ({ text: 'hello', tidied: true }) }));
  vi.stubGlobal('fetch', fetch);
  const blob = new Blob(['audio']); const signal = new AbortController().signal;
  expect(await transcribeClip(blob, 'audio/webm', true, signal)).toEqual({ text: 'hello', tidied: true });
  expect(fetch).toHaveBeenCalledWith('/api/voice/transcribe?tidy=true', expect.objectContaining({ body: blob, signal, headers: expect.objectContaining({ 'Content-Type': 'audio/webm' }) }));
  await transcribeClip(blob, 'audio/webm', false);
  expect(fetch.mock.calls.at(-1)?.[0]).toBe('/api/voice/transcribe');
});
it('distinguishes permanent refusals from transient failures', () => {
  for (const code of [400, 413, 422]) expect(transcribeRetryable(new ApiError('', code, ''))).toBe(false);
  for (const code of [429, 500, 502]) expect(transcribeRetryable(new ApiError('', code, ''))).toBe(true);
  expect(transcribeRetryable(new Error())).toBe(true);
});
