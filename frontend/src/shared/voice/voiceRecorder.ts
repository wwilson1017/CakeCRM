/** Shared recording arithmetic, port of cake_os #3832. */
export const AUDIO_MIME_PREFERENCE = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm'] as const;
export const DICTATION_MAX_SECONDS = 3 * 60;
export const DICTATION_BITS_PER_SECOND = 32_000;
// Equals backend/voice/service.MAX_AUDIO_BYTES; leave room for the final chunk.
export const DICTATION_UPLOAD_CAP_BYTES = 8 * 1024 * 1024;
export const DICTATION_MAX_BYTES = Math.floor(DICTATION_UPLOAD_CAP_BYTES * 0.85);

export function pickAudioMimeType(isSupported: (type: string) => boolean): string | null {
  for (const type of AUDIO_MIME_PREFERENCE) {
    try {
      if (isSupported(type)) return type;
    } catch {
      // A browser may throw rather than return false on a type it cannot parse. Treat
      // that as "not supported" and keep walking, or one bad entry ends the cascade.
    }
  }
  return null;
}

export function elapsedSeconds(startedAt: number, now: number): number {
  return Math.max(0, Math.floor((now - startedAt) / 1000));
}

export function formatDuration(totalSeconds: number): string {
  const safe = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(safe / 60);
  return `${minutes}:${String(safe % 60).padStart(2, '0')}`;
}

export function rmsLevel(samples: ArrayLike<number>): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) {
    const v = (samples[i] - 128) / 128;
    sum += v * v;
  }
  return Math.min(1, Math.sqrt(sum / samples.length) * 4);
}
