import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../core/api/client';
import { uploadPath } from './chatterAttachments';
import { postNoteWithAttachments, uploadAttachments } from './postNote';
import type { UploadFn } from './postNote';

interface Pending {
  noteId: number;
  files: File[];
}

export interface ChatterPost {
  /** Pass straight to `<NoteComposer onSubmit>`. Rejects to keep the user's text staged. */
  post: (text: string, files: File[]) => Promise<void>;
  retryFiles: File[] | undefined;
  retry: () => void;
  discardRetry: () => void;
  /** Ready-made banner copy, or null when there is nothing to say. */
  notice: string | null;
}

/** The real uploader: multipart through the shared client, which already sets the boundary. */
const defaultUpload: UploadFn = (path, file) => {
  const form = new FormData();
  form.append('file', file);
  return api(path, { method: 'POST', body: form });
};

/**
 * The post-a-note-with-attachments state machine (issue #57).
 *
 * Ported from `cake_os/frontend/src/shared/chatter/useChatterPost.ts` with its four-surface
 * `surface` parameter dropped — CakeCRM has exactly one chatter table and one upload
 * endpoint.
 *
 * `entityKey` is the guard worth keeping in full. Retry state is entity-scoped DATA — it
 * names a note id on a specific record — so it must be abandoned the moment the panel
 * points at a different one. The deal detail sheet does NOT remount between deals, which
 * in the blueprint meant a failed upload on deal A left a live "Retry" button on deal B
 * that would have filed the photo onto A's note, silently, behind a success affordance.
 */
export function useChatterPost(
  entityKey: string,
  createNote: (message: string) => Promise<{ id: number }>,
  onPosted: () => void | Promise<void>,
  upload: UploadFn = defaultUpload,
): ChatterPost {
  const [pending, setPending] = useState<Pending | null>(null);

  // Derived-during-render reset (the React-recommended shape) rather than an effect, so a
  // stale banner is never painted for even one frame on the new entity.
  const [lastEntity, setLastEntity] = useState(entityKey);
  // The ref is what an IN-FLIGHT post/retry checks when it resolves. Clearing state on the
  // switch is not enough on its own: an upload started on deal A can finish AFTER the user
  // moved to deal B, and its `setPending` would then land a Retry button — pointing at A's
  // note — on B. Same for `onPosted`, whose captured reload would paint A's thread into
  // B's panel. An upload takes seconds, so this window is ordinary use, not a rare race.
  const currentEntity = useRef(entityKey);
  // Written in an effect, not during render: a ref write during render is a lint error and
  // a real hazard under a discarded StrictMode pass. Effects commit long before any
  // network call resolves, so the ref is always current by the time one reads it.
  useEffect(() => { currentEntity.current = entityKey; }, [entityKey]);

  if (lastEntity !== entityKey) {
    setLastEntity(entityKey);
    if (pending) setPending(null);
  }

  const post = useCallback(
    async (text: string, files: File[]) => {
      const calledFor = currentEntity.current;
      // Deliberately NOT caught: a failure to create the note must reject so the composer
      // keeps the text the user typed.
      const { noteId, failures } = await postNoteWithAttachments({
        text, files, createNote, uploadPath, upload,
      });
      // The note and its files landed on `calledFor`. If the panel has moved on, that is
      // all still true — it just isn't this screen's business any more.
      if (currentEntity.current !== calledFor) return;
      setPending(failures.length ? { noteId, files: failures.map(f => f.file) } : null);
      await onPosted();
    },
    [createNote, onPosted, upload],
  );

  const retry = useCallback(async () => {
    if (!pending) return;
    const calledFor = currentEntity.current;
    const failures = await uploadAttachments(pending.noteId, pending.files, uploadPath, upload);
    if (currentEntity.current !== calledFor) return;
    setPending(failures.length ? { noteId: pending.noteId, files: failures.map(f => f.file) } : null);
    await onPosted();
  }, [pending, onPosted, upload]);

  const count = pending?.files.length ?? 0;
  return {
    post,
    retryFiles: pending?.files,
    retry: () => void retry(),
    discardRetry: () => setPending(null),
    notice: count ? `${count} attachment${count === 1 ? '' : 's'} didn't upload.` : null,
  };
}
