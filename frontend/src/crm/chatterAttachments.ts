/**
 * Staged-attachment rules for the note composer (issue #57) — pure, so they test in Node.
 *
 * Ported from `cake_os/frontend/src/shared/chatter/attachments.ts`. The headline flow is a
 * rep copying a photo out of a customer's email and pasting it into a deal note;
 * everything here exists to make that paste land intact.
 *
 * The blueprint's client-side downscale half (`shouldDownscale`/`prepareAttachment`, which
 * re-encode large JPEG/HEIC through a canvas) is deliberately NOT ported: it is a storage
 * optimization rather than a correctness need, the 10 MB cap already bounds the cost, and
 * it would drag a canvas re-encode module into scope. Port it behind `shouldDownscale`
 * unchanged if attachment volume ever justifies it.
 */

/** Mirrors attachment_service.MAX_ATTACHMENTS_PER_NOTE — the server enforces it too. */
export const MAX_ATTACHMENTS = 10;

/** Mirrors attachment_service.MAX_ATTACHMENT_BYTES (10 MB). */
export const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;

/** Human-readable size, exported so chips and error copy can never disagree. */
export function formatBytes(bytes: number): string {
  return bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} MB`
    : `${Math.ceil(bytes / 1024)} KB`;
}

/**
 * Files carried by a paste or drop, or `[]`.
 *
 * MUST be called synchronously inside the event handler: a `DataTransferItemList` is
 * neutered as soon as the handler returns, so any `await` before this runs yields an
 * empty list.
 */
export function collectPastedFiles(data: DataTransfer | null | undefined): File[] {
  if (!data) return [];
  const out: File[] = [];
  // `items` carries the richer view (a pasted screenshot has no entry in `files` on some
  // browsers); `files` is the fallback for drops and older paths.
  if (data.items && data.items.length > 0) {
    for (const item of Array.from(data.items)) {
      if (item.kind !== 'file') continue;
      const file = item.getAsFile();
      if (file) out.push(file);
    }
  }
  if (out.length === 0 && data.files && data.files.length > 0) {
    out.push(...Array.from(data.files));
  }
  return out;
}

/**
 * Whether a paste that produced files should also be allowed to insert its text.
 *
 * Copying an image out of an email usually carries prose AND the picture, and swallowing
 * the paste would drop the words the user actually wanted. Copying a file in Finder
 * carries only the filename as text, which is noise. So: consume the paste only when
 * there is nothing meaningful to type.
 */
export function shouldConsumePaste(accepted: number, plainText: string): boolean {
  return accepted > 0 && plainText.trim() === '';
}

export interface ValidationResult {
  accepted: File[];
  errors: string[];
}

/**
 * Apply the staging rules to incoming files.
 *
 * Every rejection is reported. A silent skip is the worst outcome here: every Chrome
 * screenshot is named `image.png`, so the duplicate rule fires on genuinely different
 * files often enough that the user has to be told which one was dropped.
 */
export function validateAttachmentFiles(existing: File[], incoming: File[]): ValidationResult {
  const accepted: File[] = [];
  const errors: string[] = [];
  const seen = new Set(existing.map(f => `${f.name}:${f.size}`));

  for (const file of incoming) {
    if (existing.length + accepted.length >= MAX_ATTACHMENTS) {
      errors.push(`Only ${MAX_ATTACHMENTS} attachments per note — "${file.name}" wasn't added.`);
      continue;
    }
    // Chrome hands a dropped FOLDER over as a zero-byte entry with no type. Saying "empty
    // file" there sends the user hunting for a corrupt file that does not exist.
    if (file.size === 0 && !file.type) {
      errors.push(`Folders can't be attached — drop the files inside "${file.name}" instead.`);
      continue;
    }
    if (file.size === 0) {
      errors.push(`"${file.name}" is empty.`);
      continue;
    }
    if (file.size > MAX_ATTACHMENT_BYTES) {
      errors.push(
        `"${file.name}" is ${formatBytes(file.size)} — the limit is ${formatBytes(MAX_ATTACHMENT_BYTES)}.`,
      );
      continue;
    }
    const key = `${file.name}:${file.size}`;
    if (seen.has(key)) {
      errors.push(`"${file.name}" is already attached.`);
      continue;
    }
    seen.add(key);
    accepted.push(file);
  }
  return { accepted, errors };
}

/** Longest fallback note text generated for a files-only post. */
export const FALLBACK_MESSAGE_MAX = 300;

/**
 * Message body for a post that carries files but no typed text.
 *
 * `chatter_service.add_note` rejects an empty message, and widening that to allow one
 * would be a much wider blast radius than generating a sensible line here.
 */
export function attachmentFallbackMessage(files: File[]): string {
  if (files.length === 0) return '';
  const names = files.map(f => f.name || 'attachment');
  let text = `Attached: ${names.join(', ')}`;
  if (text.length > FALLBACK_MESSAGE_MAX) {
    text = `${text.slice(0, FALLBACK_MESSAGE_MAX - 1)}…`;
  }
  return text;
}

/** True for a type the server stores as a real image and offers a thumbnail for. */
export function isImage(mimeType: string): boolean {
  return (mimeType || '').toLowerCase().startsWith('image/');
}

/**
 * Mirrors `attachment_service.INLINE_IMAGE_MIMES` — the ONLY types the server keeps under
 * a real image type and thumbnails.
 *
 * Distinct from `isImage()` on purpose. `isImage` answers "did the server decide this is an
 * image", asked of a STORED attachment's server-derived type. This answers "will the server
 * keep this as an image", asked of a staged file's browser-guessed `File.type` — so
 * previewing an SVG, which the server stores as an inert octet-stream, would promise a
 * picture the posted note cannot show.
 */
export const PREVIEWABLE_IMAGE_MIMES = new Set([
  'image/jpeg', 'image/png', 'image/gif', 'image/webp',
]);

/** The authenticated byte endpoints for one attachment. Never used as a bare `<img src>`. */
export const thumbPath = (id: number): string => `/api/crm/chatter/attachments/${id}/thumb`;
export const filePath = (id: number): string => `/api/crm/chatter/attachments/${id}/file`;
export const uploadPath = (noteId: number): string =>
  `/api/crm/chatter/note/${noteId}/attachments`;
