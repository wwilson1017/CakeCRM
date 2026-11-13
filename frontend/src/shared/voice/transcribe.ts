import { api, ApiError } from '../../core/api/client';
export interface TranscribeResult { text: string; tidied: boolean }
export function transcribeClip(blob: Blob, mime: string, tidy: boolean, signal?: AbortSignal): Promise<TranscribeResult> {
  return api<TranscribeResult>('/api/voice/transcribe' + (tidy ? '?tidy=true' : ''), {
    method: 'POST', body: blob, headers: { 'Content-Type': mime }, signal,
  });
}
/** Refusals that cannot succeed on retry; network and transient failures can. */
export function transcribeRetryable(err: unknown): boolean {
  return !(err instanceof ApiError && [400, 413, 422].includes(err.status));
}
