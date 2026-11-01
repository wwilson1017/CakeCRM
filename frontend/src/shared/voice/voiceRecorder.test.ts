import { describe, expect, it } from 'vitest';
import { audioFileExtension, elapsedSeconds, formatDuration, pickAudioMimeType, rmsLevel } from './voiceRecorder';
describe('recording arithmetic', () => {
  it('prefers mp4, skips throwing candidates and hides when none work', () => {
    expect(pickAudioMimeType(() => true)).toBe('audio/mp4');
    expect(pickAudioMimeType(t => { if (t === 'audio/mp4') throw Error(); return t === 'audio/webm'; })).toBe('audio/webm');
    expect(pickAudioMimeType(() => false)).toBeNull();
  });
  it('normalizes extensions and measures duration and level', () => {
    expect(audioFileExtension('audio/webm;codecs=opus')).toBe('webm');
    expect(audioFileExtension('audio/mp4')).toBe('m4a');
    expect(formatDuration(65)).toBe('1:05');
    expect(elapsedSeconds(1000, 3500)).toBe(2);
    expect(rmsLevel([128, 128])).toBe(0);
    expect(rmsLevel([0, 255, 0, 255])).toBe(1);
  });
});
