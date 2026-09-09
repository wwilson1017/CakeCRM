import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import {
  collectPastedFiles,
  formatBytes,
  MAX_ATTACHMENTS,
  PREVIEWABLE_IMAGE_MIMES,
  shouldConsumePaste,
  validateAttachmentFiles,
} from '../chatterAttachments';
import {
  COMPOSER_MIN_HEIGHT_PX, composerKeyAction, MAX_NOTE_LEN, nextComposerHeight,
} from '../chatterComposer';
import {
  ACCENT, ACCENT_INK, ACCENT_TEXT, CORAL_FILL, CORAL_TEXT, INK, INK_DIM, INK_MUTE, LINE, LINE_STRONG,
  inputStyle, mono, tint,
} from '../../shared/styles';

export interface NoteComposerProps {
  /** Rejects to keep the text and files staged; resolves to clear them. */
  onSubmit: (text: string, files: File[]) => Promise<void>;
  /** Files still staged after a partial failure — Retry re-sends exactly these. */
  retryFiles?: File[];
  onRetry?: () => void;
  onDiscardRetry?: () => void;
  /** Banner text shown above the box (upload failures, mostly). */
  notice?: string | null;
  disabled?: boolean;
}

/** One staged file plus its preview URL, so the URL can be revoked exactly once. */
interface Staged {
  file: File;
  previewUrl: string | null;
}

// Preview only what the SERVER will keep as an image — see PREVIEWABLE_IMAGE_MIMES.
const stage = (file: File): Staged => ({
  file,
  previewUrl: PREVIEWABLE_IMAGE_MIMES.has((file.type || '').toLowerCase())
    ? URL.createObjectURL(file)
    : null,
});

/**
 * The note composer (issue #57) — the readable-composer half of the cake_os port.
 *
 * Replaces the two-row `<textarea>` NotesThread used to inline: a ~6-line auto-growing
 * box, Enter for a newline with the Post button (or Cmd/Ctrl+Enter) to submit, and
 * attachments by paste, drag-drop or the paperclip.
 *
 * Ported from `cake_os/frontend/src/shared/chatter/ChatterComposer.tsx`, restyled onto
 * CakeCRM's inline theme tokens (the blueprint's Tailwind `bg-cream`/`text-charcoal`
 * classes were removed by #54) and with its `allowAttachments` flag dropped — that exists
 * for the blueprint's fourth surface, which has no attachment endpoints. Every surface
 * here does.
 */
export function NoteComposer({
  onSubmit, retryFiles, onRetry, onDiscardRetry, notice, disabled = false,
}: NoteComposerProps) {
  const [text, setText] = useState('');
  const [staged, setStaged] = useState<Staged[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Revoke on unmount only. Per-chip revocation happens in the remove/submit paths, so
  // this reads the CURRENT list through a ref rather than re-running whenever the list
  // changes — which would revoke a URL still on screen.
  const stagedRef = useRef(staged);
  useEffect(() => { stagedRef.current = staged; }, [staged]);
  useEffect(() => () => {
    for (const item of stagedRef.current) {
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
    }
  }, []);

  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    // Collapse before measuring, or scrollHeight never shrinks when text is deleted.
    el.style.height = 'auto';
    el.style.height = `${nextComposerHeight(el.scrollHeight)}px`;
  }, [text]);

  /**
   * Stage `incoming`, returning how many were accepted.
   *
   * The validation runs HERE, not inside the `setStaged` updater. React only invokes an
   * updater synchronously on its eager-dispatch fast path — which is skipped the moment
   * any other setState has already run on this fiber in the same handler (`handleDrop`
   * calls `setDragging(false)` first, which is exactly that case) — so a count assigned
   * inside the updater and read after it can silently come back 0. `handlePaste` decides
   * whether to `preventDefault()` from this number, so reading it wrong means swallowing
   * (or failing to swallow) the user's paste.
   */
  const addFiles = useCallback((incoming: File[]) => {
    if (incoming.length === 0) return 0;
    const { accepted, errors: rejected } = validateAttachmentFiles(
      stagedRef.current.map(s => s.file), incoming,
    );
    setErrors(rejected);
    if (accepted.length) {
      const next = accepted.map(stage);
      // Keep the ref in step immediately: two addFiles calls in one handler must not both
      // validate against the same pre-call list and blow past MAX_ATTACHMENTS.
      stagedRef.current = [...stagedRef.current, ...next];
      setStaged(prev => [...prev, ...next]);
    }
    return accepted.length;
  }, []);

  const removeStaged = useCallback((index: number) => {
    const target = stagedRef.current[index];
    if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl);
    stagedRef.current = stagedRef.current.filter((_, i) => i !== index);
    setStaged(prev => prev.filter((_, i) => i !== index));
  }, []);

  const handlePaste = useCallback((e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    // Synchronous: a DataTransferItemList is neutered once this handler returns.
    const files = collectPastedFiles(e.clipboardData);
    if (files.length === 0) return;
    const plain = e.clipboardData?.getData('text/plain') ?? '';
    const accepted = addFiles(files);
    // Only swallow the paste when there is no text worth keeping — an email copy usually
    // carries the prose AND the picture, and the user wants both.
    if (shouldConsumePaste(accepted, plain)) e.preventDefault();
  }, [addFiles]);

  const handleDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    addFiles(collectPastedFiles(e.dataTransfer));
  }, [addFiles]);

  const hasRetry = (retryFiles?.length ?? 0) > 0;

  const submit = useCallback(async () => {
    // An outstanding retry batch names a note that ALREADY exists, and the composer holds
    // exactly one. Posting again would abandon it — silently, and with a success
    // affordance: the banner simply disappears and the file is gone with no error and no
    // way back to it. So the batch has to be settled (Retry or Discard, both one click
    // away in the banner) first.
    if (busy || disabled || hasRetry) return;
    const sending = staged;
    const sentText = text;
    const files = sending.map(s => s.file);
    if (!text.trim() && files.length === 0) return;
    setBusy(true);
    setErrors([]);
    try {
      await onSubmit(text, files);
      // Clear only what was SUBMITTED. Uploading several photos takes seconds and the
      // textarea stays live throughout, so a file pasted during that window would
      // otherwise be wiped without ever being uploaded — and its object URL leaked, since
      // the unmount cleanup reads the ref a blanket reset had already emptied.
      const submitted = new Set(sending);
      for (const item of sending) {
        if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
      }
      stagedRef.current = stagedRef.current.filter(item => !submitted.has(item));
      // Same rule for the text: clear only what was actually sent.
      setText(prev => (prev === sentText ? '' : prev));
      setStaged(prev => prev.filter(item => !submitted.has(item)));
    } catch (err) {
      setErrors([err instanceof Error ? err.message : 'Could not post that note.']);
    } finally {
      setBusy(false);
    }
  }, [busy, disabled, hasRetry, onSubmit, staged, text]);

  const canSubmit = !busy && !disabled && !hasRetry && (text.trim().length > 0 || staged.length > 0);

  return (
    <div
      style={{
        border: `1px solid ${dragging ? ACCENT : LINE}`,
        background: dragging ? tint(ACCENT, 6) : 'transparent',
        borderRadius: 6, padding: 8, marginBottom: 16,
        transition: 'border-color 120ms, background 120ms',
      }}
      onDragOver={e => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
    >
      {notice && (
        <p style={{
          margin: '0 0 8px', padding: '6px 8px', borderRadius: 4,
          background: tint(CORAL_FILL, 12), color: CORAL_TEXT, fontSize: 12,
        }}>
          {notice}
          {hasRetry && (
            <>
              {' '}
              <button type="button" onClick={onRetry} style={{ ...linkBtn, color: CORAL_TEXT, fontWeight: 600 }}>
                Retry {retryFiles?.length} file{retryFiles?.length === 1 ? '' : 's'}
              </button>
              {' · '}
              <button type="button" onClick={onDiscardRetry} style={{ ...linkBtn, color: CORAL_TEXT }}>
                Discard
              </button>
            </>
          )}
        </p>
      )}

      <textarea
        ref={textareaRef}
        placeholder="Add a note…"
        value={text}
        onChange={e => setText(e.target.value)}
        onPaste={handlePaste}
        // `isComposing` off the NATIVE event: React's synthetic KeyboardEvent does not
        // carry it, and passing the synthetic event makes the IME guard silently dead.
        onKeyDown={e => {
          if (composerKeyAction({
            key: e.key, metaKey: e.metaKey, ctrlKey: e.ctrlKey, altKey: e.altKey,
            isComposing: e.nativeEvent.isComposing,
          }) === 'submit') { e.preventDefault(); void submit(); }
        }}
        maxLength={MAX_NOTE_LEN}
        disabled={disabled}
        style={{
          ...inputStyle, width: '100%', fontSize: 13, lineHeight: 1.5,
          minHeight: COMPOSER_MIN_HEIGHT_PX, resize: 'none', overflowY: 'auto',
          border: 'none', background: 'transparent', padding: 8,
        }}
      />

      {staged.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
          {staged.map((item, i) => (
            <span key={`${item.file.name}:${item.file.size}:${i}`} style={{
              display: 'inline-flex', alignItems: 'center', gap: 6,
              border: `1px solid ${LINE_STRONG}`, borderRadius: 4, padding: '3px 6px',
              fontSize: 11, color: INK_MUTE, maxWidth: 240,
            }}>
              {item.previewUrl
                ? <img src={item.previewUrl} alt="" style={{
                    width: 24, height: 24, objectFit: 'cover', borderRadius: 2, flexShrink: 0,
                  }} />
                : null}
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {item.file.name}
              </span>
              <span style={{ ...mono(10), flexShrink: 0 }}>{formatBytes(item.file.size)}</span>
              <button
                type="button"
                onClick={() => removeStaged(i)}
                aria-label={`Remove ${item.file.name}`}
                style={{ ...linkBtn, color: INK_DIM, fontSize: 13, lineHeight: 1, flexShrink: 0 }}
              >×</button>
            </span>
          ))}
        </div>
      )}

      {errors.length > 0 && (
        <ul style={{ margin: '6px 0 0', paddingLeft: 16, color: CORAL_TEXT, fontSize: 11 }}>
          {errors.map((e, i) => <li key={i}>{e}</li>)}
        </ul>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 8 }}>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          onChange={e => {
            addFiles(Array.from(e.target.files ?? []));
            // Reset so re-picking the SAME file fires change again.
            e.target.value = '';
          }}
          style={{ display: 'none' }}
        />
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={disabled || staged.length >= MAX_ATTACHMENTS}
          style={{
            ...linkBtn, color: ACCENT_TEXT, fontSize: 12,
            opacity: staged.length >= MAX_ATTACHMENTS ? 0.5 : 1,
          }}
        >Attach</button>
        <span style={{ ...mono(10), color: INK_DIM, flex: 1 }}>
          Paste or drop files · Cmd/Ctrl+Enter to post
        </span>
        <button
          type="button"
          onClick={() => void submit()}
          disabled={!canSubmit}
          style={{
            background: ACCENT, color: ACCENT_INK, border: 'none',
            padding: '6px 16px', borderRadius: 4, fontSize: 13, fontWeight: 500,
            cursor: canSubmit ? 'pointer' : 'default', opacity: canSubmit ? 1 : 0.5,
          }}
        >{busy ? 'Posting…' : 'Post'}</button>
      </div>
    </div>
  );
}

const linkBtn = {
  background: 'none', border: 'none', color: INK,
  cursor: 'pointer', padding: 0, font: 'inherit',
} as const;
