/**
 * Post a chatter note, then upload its staged attachments (issue #57).
 *
 * Note first, files second. The alternative — upload to a staging area, then create the
 * note — needs orphan rows for drafts nobody posts, and a staging/confirm lifecycle this
 * feature deliberately does not build.
 *
 * The trade-off is a real partial-failure window: the note can land and an upload can
 * fail. That is why this returns the failures ALONG WITH the note id instead of throwing
 * — the composer keeps those files staged and offers Retry against the note that now
 * exists. Without the id, "attach to an existing note" would be the only recovery.
 *
 * Ported from `cake_os/frontend/src/shared/chatter/postNote.ts`, minus its client-side
 * downscale step (see `chatterAttachments.ts`) and its `role` upload parameter (CakeCRM
 * has one upload endpoint, not a platform media service).
 */

import { attachmentFallbackMessage } from './chatterAttachments';

export interface AttachmentUploadFailure {
  file: File;
  error: string;
}

export interface PostNoteResult {
  noteId: number;
  failures: AttachmentUploadFailure[];
}

/** Injected so these functions stay pure and testable in Node. */
export type UploadFn = (path: string, file: File) => Promise<unknown>;

export interface PostNoteOptions {
  text: string;
  files: File[];
  /** User IDs picked with the composer's @ menu (#235); rides the create request. */
  mentions?: number[];
  /** Creates the note through the existing chatter endpoint. */
  createNote: (message: string, mentions: number[]) => Promise<{ id: number }>;
  uploadPath: (noteId: number) => string;
  upload: UploadFn;
}

/**
 * Upload `files` to an existing note. Used for the first post and, unchanged, for Retry.
 *
 * Sequential rather than parallel: these are photos off a phone or out of an email, the
 * server serializes per-note anyway (every upload locks the parent note row), and a
 * failure part-way through leaves an obvious "these N are still staged" state instead of
 * a scattered one.
 */
export async function uploadAttachments(
  noteId: number,
  files: File[],
  uploadPath: (noteId: number) => string,
  upload: UploadFn,
): Promise<AttachmentUploadFailure[]> {
  const failures: AttachmentUploadFailure[] = [];
  for (const file of files) {
    try {
      await upload(uploadPath(noteId), file);
    } catch (err) {
      failures.push({ file, error: err instanceof Error ? err.message : 'Upload failed' });
    }
  }
  return failures;
}

export async function postNoteWithAttachments({
  text,
  files,
  mentions = [],
  createNote,
  uploadPath,
  upload,
}: PostNoteOptions): Promise<PostNoteResult> {
  const typed = text.trim();
  // add_note rejects an empty message, so a files-only post needs a body.
  const message = typed || attachmentFallbackMessage(files);
  // Deliberately NOT caught: if the note itself fails there is nothing to attach to, and
  // the composer must keep the user's text.
  const note = await createNote(message, mentions);
  if (files.length === 0) return { noteId: note.id, failures: [] };
  return { noteId: note.id, failures: await uploadAttachments(note.id, files, uploadPath, upload) };
}
