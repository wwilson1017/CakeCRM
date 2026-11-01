import { useCallback, useEffect, useRef, useState } from 'react';
import { elapsedSeconds, pickAudioMimeType, rmsLevel } from './voiceRecorder';

/** Browser recorder plumbing, port of cake_os #3832; request-only audio. */
export interface RecordedClip {
  blob: Blob;
  mime: string;
  durationSeconds: number;
}

/** `limit` = the time or byte cap stopped it, not the user. */
export type StopReason = 'stopped' | 'limit';

export type RecorderError = 'unsupported' | 'denied' | 'start-failed' | 'failed' | 'empty';

export function useAudioRecorder({
  maxSeconds, maxBytes, bitsPerSecond, withLevel = false, onClip, onError,
}: {
  maxSeconds: number;
  /** Stop once the recorded bytes reach this. Needs timesliced chunks, so it costs nothing unset. */
  maxBytes?: number;
  /** A hint — Safari may ignore it, which is what `maxBytes` is for. */
  bitsPerSecond?: number;
  /** Sample a 0–1 input level for a live wave. Off for callers that draw none. */
  withLevel?: boolean;
  onClip: (clip: RecordedClip, reason: StopReason) => void;
  /** Every failure, as it happens — also readable afterwards as `error`. */
  onError?: (error: RecorderError) => void;
}) {
  /**
   * Capability, resolved ONCE in a lazy initializer rather than in an effect.
   *
   * It cannot be computed during module evaluation (`MediaRecorder` is absent in jsdom and on
   * older iOS, and `navigator` does not exist at all under a node import), and an effect that
   * `setState`s would be a cascading render for a value that can never change during a mount.
   */
  const [supported] = useState(() => (
    typeof window !== 'undefined'
    && typeof window.MediaRecorder !== 'undefined'
    && !!navigator.mediaDevices?.getUserMedia
    && pickAudioMimeType(t => window.MediaRecorder.isTypeSupported(t)) !== null
  ));
  const [recording, setRecording] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [level, setLevel] = useState(0);
  const [error, setError] = useState<RecorderError | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const bytesRef = useRef(0);
  const startedAtRef = useRef(0);
  const stopReasonRef = useRef<StopReason>('stopped');
  const onClipRef = useRef(onClip);
  const onErrorRef = useRef(onError);
  useEffect(() => {
    onClipRef.current = onClip;
    onErrorRef.current = onError;
  }, [onClip, onError]);
  const fail = useCallback((code: RecorderError) => {
    setError(code);
    onErrorRef.current?.(code);
  }, []);
  /**
   * Bumped on every start, cancel and unmount, so an in-flight `getUserMedia` can tell whether it
   * is still wanted when it finally resolves.
   *
   * Cleanup cannot cancel a pending permission prompt: it runs, and THEN the continuation assigns
   * `streamRef` and calls `recorder.start()` on a component that no longer exists — leaving the
   * microphone live on a shared device with no UI to turn it off. A rapid double-tap has the
   * same shape, acquiring two streams while only the second is retained. Both are closed by
   * checking this token after the await.
   */
  const generationRef = useRef(0);

  /**
   * Stopping the tracks is what turns the browser's recording indicator off. A live mic left open
   * on a shared device is the failure worth guarding hardest, so this is idempotent and runs
   * from every exit: stop, cancel, error and unmount.
   */
  const releaseStream = useCallback(() => {
    streamRef.current?.getTracks().forEach(track => track.stop());
    streamRef.current = null;
    analyserRef.current = null;
    const ctx = audioCtxRef.current;
    audioCtxRef.current = null;
    void ctx?.close().catch(() => undefined);
  }, []);

  const stopWith = useCallback((reason: StopReason) => {
    stopReasonRef.current = reason;
    if (recorderRef.current && recorderRef.current.state !== 'inactive') {
      recorderRef.current.stop();
    }
  }, []);

  const stop = useCallback(() => stopWith('stopped'), [stopWith]);

  /** Abandon the recording: no clip, mic released, and a permission still pending is dropped. */
  const cancel = useCallback(() => {
    generationRef.current += 1;
    if (recorderRef.current && recorderRef.current.state !== 'inactive') {
      recorderRef.current.stop();
    }
    recorderRef.current = null;
    chunksRef.current = [];
    releaseStream();
    setRecording(false);
    setRequesting(false);
    setSeconds(0);
    setLevel(0);
  }, [releaseStream]);

  useEffect(() => () => {
    // Invalidate any permission request still in flight BEFORE tearing down, so its continuation
    // releases the stream it is about to receive instead of starting on a dead component.
    generationRef.current += 1;
    if (recorderRef.current && recorderRef.current.state !== 'inactive') {
      recorderRef.current.stop();
    }
    releaseStream();
  }, [releaseStream]);

  // The running timer, the cap and the level meter. Driven off the START TIMESTAMP rather than
  // a counter: a throttled background tab fires intervals late, so an accumulator would
  // under-count exactly when the cap matters.
  useEffect(() => {
    if (!recording) return;
    const buffer = new Uint8Array(256);
    const id = window.setInterval(() => {
      const elapsed = elapsedSeconds(startedAtRef.current, Date.now());
      setSeconds(elapsed);
      if (elapsed >= maxSeconds) stopWith('limit');
      const analyser = analyserRef.current;
      if (analyser) {
        analyser.getByteTimeDomainData(buffer);
        setLevel(rmsLevel(buffer));
      }
    }, 100);
    return () => window.clearInterval(id);
  }, [recording, maxSeconds, stopWith]);

  /** Resolves true once the recorder is genuinely running; false on any failure or supersession. */
  const start = useCallback(async (): Promise<boolean> => {
    // Already live. A start during a pending permission is NOT refused here: it supersedes the
    // first through the generation token, whose continuation then releases its own stream.
    if (recorderRef.current?.state === 'recording' || streamRef.current) return false;
    setError(null);
    const mimeType = typeof window !== 'undefined' && typeof window.MediaRecorder !== 'undefined'
      ? pickAudioMimeType(t => window.MediaRecorder.isTypeSupported(t))
      : null;
    if (!mimeType) {
      fail('unsupported');
      return false;
    }

    const generation = generationRef.current + 1;
    generationRef.current = generation;
    // Created BEFORE the first await: iOS only lets an AudioContext run when it starts inside the
    // user's gesture, and `start()` is called synchronously from the press. Best effort — a
    // context that cannot start only flattens the wave .
    let ctx: AudioContext | null = null;
    if (withLevel) {
      try {
        ctx = new AudioContext();
        void ctx.resume().catch(() => undefined);
      } catch {
        ctx = null;
      }
    }
    const dropCtx = () => { void ctx?.close().catch(() => undefined); };
    setRequesting(true);
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      dropCtx();
      // Denied, dismissed, or no input device — all the same instruction to the user.
      if (generationRef.current === generation) {
        fail('denied');
        setRequesting(false);
      }
      return false;
    }

    // The await above may have outlived this component (unmount), been cancelled, or been
    // superseded by a newer start. Either way the stream is unwanted — release it here or the
    // microphone stays live with nothing on screen able to stop it.
    if (generationRef.current !== generation) {
      stream.getTracks().forEach(track => track.stop());
      dropCtx();
      return false;
    }

    streamRef.current = stream;
    chunksRef.current = [];
    bytesRef.current = 0;
    stopReasonRef.current = 'stopped';
    let recorder: MediaRecorder;
    try {
      recorder = new MediaRecorder(stream, bitsPerSecond ? { mimeType, audioBitsPerSecond: bitsPerSecond } : { mimeType });
    } catch {
      // A constructor throw (an unexpected mimeType, a revoked device) must not leave the stream
      // we already hold running.
      dropCtx();
      releaseStream();
      setRequesting(false);
      fail('start-failed');
      return false;
    }
    recorderRef.current = recorder;

    audioCtxRef.current = ctx;
    if (ctx && withLevel) {
      try {
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 256;
        ctx.createMediaStreamSource(stream).connect(analyser);
        analyserRef.current = analyser;
      } catch {
        analyserRef.current = null;
      }
    }

    recorder.ondataavailable = e => {
      // A cancelled recorder flushes one last chunk; it must not land in a newer recording.
      if (e.data.size === 0 || generationRef.current !== generation) return;
      chunksRef.current.push(e.data);
      bytesRef.current += e.data.size;
      if (maxBytes && bytesRef.current >= maxBytes) stopWith('limit');
    };
    recorder.onerror = () => {
      if (generationRef.current !== generation) return;
      // Browsers still emit the final `dataavailable` and `stop` after an error; retiring this
      // generation is what keeps that partial recording from arriving as a clip.
      generationRef.current += 1;
      recorderRef.current = null;
      chunksRef.current = [];
      fail('failed');
      setRecording(false);
      releaseStream();
    };
    recorder.onstop = () => {
      // `stop()` runs from cancel and unmount too, and `onstop` fires asynchronously — so this
      // can run after the caller stopped wanting a clip, possibly after a NEWER recording took
      // over `streamRef`. Bail before touching anything: cancel and unmount already released
      // this recording's stream, and releasing here would kill the new one.
      if (generationRef.current !== generation) return;
      releaseStream();
      const chunks = chunksRef.current;
      chunksRef.current = [];
      recorderRef.current = null;
      setRecording(false);
      setLevel(0);
      const durationSeconds = elapsedSeconds(startedAtRef.current, Date.now());
      const blob = new Blob(chunks, { type: mimeType });
      if (blob.size === 0) {
        fail('empty');
        return;
      }
      onClipRef.current(
        { blob, mime: mimeType.split(';', 1)[0], durationSeconds },
        stopReasonRef.current,
      );
    };

    startedAtRef.current = Date.now();
    setSeconds(0);
    setRequesting(false);
    setRecording(true);
    try {
      // Timesliced only when a byte cap needs counting; otherwise one chunk at stop.
      if (maxBytes) recorder.start(1000);
      else recorder.start();
    } catch {
      // Same reasoning as the constructor: a failed start must not strand a live mic.
      recorderRef.current = null;
      setRecording(false);
      releaseStream();
      fail('start-failed');
      return false;
    }
    return true;
  }, [bitsPerSecond, fail, maxBytes, releaseStream, stopWith, withLevel]);

  return { supported, recording, requesting, seconds, level, error, start, stop, cancel };
}
