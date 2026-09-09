// Staging rules for the note composer (#57). Pure — no DOM needed.
//
// These rules decide what a paste or a drop actually does, and every one of them was
// written for a failure mode the blueprint hit in the field: silently dropping a
// screenshot, swallowing the prose that came with a pasted image, or reporting a dropped
// FOLDER as a corrupt file.
import { describe, expect, it } from 'vitest';
import {
  attachmentFallbackMessage,
  collectPastedFiles,
  FALLBACK_MESSAGE_MAX,
  formatBytes,
  isImage,
  MAX_ATTACHMENT_BYTES,
  MAX_ATTACHMENTS,
  PREVIEWABLE_IMAGE_MIMES,
  shouldConsumePaste,
  validateAttachmentFiles,
} from './chatterAttachments';

const file = (name: string, size: number, type = 'image/png'): File => {
  const f = new File([''], name, { type });
  // File.size is read-only and derives from the parts, so a realistic size has to be
  // defined onto the instance rather than constructed.
  Object.defineProperty(f, 'size', { value: size });
  return f;
};

describe('validateAttachmentFiles', () => {
  it('accepts ordinary files', () => {
    const { accepted, errors } = validateAttachmentFiles([], [file('a.png', 1000)]);
    expect(accepted).toHaveLength(1);
    expect(errors).toEqual([]);
  });

  it('caps the total per note, counting what is already staged', () => {
    const existing = Array.from({ length: MAX_ATTACHMENTS - 1 }, (_, i) => file(`e${i}.png`, 10));
    const { accepted, errors } = validateAttachmentFiles(existing, [
      file('one.png', 10), file('two.png', 10),
    ]);
    expect(accepted.map(f => f.name)).toEqual(['one.png']);
    expect(errors).toHaveLength(1);
    expect(errors[0]).toContain('two.png');
  });

  it('reports a dropped folder as a folder, not as an empty file', () => {
    // Chrome hands a folder over as a zero-byte entry with NO type. Calling that "empty"
    // sends the user hunting for a corrupt file that does not exist.
    const { accepted, errors } = validateAttachmentFiles([], [file('Photos', 0, '')]);
    expect(accepted).toEqual([]);
    expect(errors[0]).toContain("Folders can't be attached");
  });

  it('rejects a genuinely empty file with a different message', () => {
    const { errors } = validateAttachmentFiles([], [file('empty.png', 0, 'image/png')]);
    expect(errors[0]).toContain('is empty');
  });

  it('rejects a file over the cap and names the limit', () => {
    const { accepted, errors } = validateAttachmentFiles(
      [], [file('huge.png', MAX_ATTACHMENT_BYTES + 1)],
    );
    expect(accepted).toEqual([]);
    expect(errors[0]).toContain('10.0 MB');
  });

  it('accepts a file exactly at the cap', () => {
    const { accepted } = validateAttachmentFiles([], [file('edge.png', MAX_ATTACHMENT_BYTES)]);
    expect(accepted).toHaveLength(1);
  });

  it('rejects a duplicate by name AND size, including within one batch', () => {
    const { accepted, errors } = validateAttachmentFiles(
      [file('image.png', 500)],
      [file('image.png', 500), file('image.png', 900), file('image.png', 900)],
    );
    // Same name, different size is a DIFFERENT file — every Chrome screenshot is
    // "image.png", so keying on the name alone would silently drop real attachments.
    expect(accepted.map(f => f.size)).toEqual([900]);
    expect(errors).toHaveLength(2);
  });

  it('reports every rejection rather than silently skipping', () => {
    const { errors } = validateAttachmentFiles([], [
      file('empty.png', 0, 'image/png'),
      file('huge.png', MAX_ATTACHMENT_BYTES + 1),
    ]);
    expect(errors).toHaveLength(2);
  });
});

describe('shouldConsumePaste', () => {
  it('swallows a paste that is only a file', () => {
    expect(shouldConsumePaste(1, '')).toBe(true);
    expect(shouldConsumePaste(1, '   ')).toBe(true);
  });

  it('lets the text through when the paste carried prose too', () => {
    // Copying an image out of an email carries the words AND the picture; swallowing the
    // paste would throw away what the user actually meant to say.
    expect(shouldConsumePaste(1, 'they sent this photo')).toBe(false);
  });

  it('never swallows a paste that produced no files', () => {
    expect(shouldConsumePaste(0, '')).toBe(false);
  });
});

describe('collectPastedFiles', () => {
  const item = (kind: string, f: File | null) => ({ kind, getAsFile: () => f });

  it('returns [] for a missing DataTransfer', () => {
    expect(collectPastedFiles(null)).toEqual([]);
    expect(collectPastedFiles(undefined)).toEqual([]);
  });

  it('prefers items, which is where a pasted screenshot shows up', () => {
    const png = file('shot.png', 10);
    const dt = { items: [item('string', null), item('file', png)], files: [] };
    expect(collectPastedFiles(dt as unknown as DataTransfer)).toEqual([png]);
  });

  it('falls back to files when items carries no file entry', () => {
    const png = file('drop.png', 10);
    const dt = { items: [item('string', null)], files: [png] };
    expect(collectPastedFiles(dt as unknown as DataTransfer)).toEqual([png]);
  });

  it('ignores an item that reports a file but hands back null', () => {
    const dt = { items: [item('file', null)], files: [] };
    expect(collectPastedFiles(dt as unknown as DataTransfer)).toEqual([]);
  });
});

describe('attachmentFallbackMessage', () => {
  it('is empty when there are no files', () => {
    expect(attachmentFallbackMessage([])).toBe('');
  });

  it('names the files, because add_note rejects an empty message', () => {
    expect(attachmentFallbackMessage([file('a.png', 1), file('b.pdf', 1)]))
      .toBe('Attached: a.png, b.pdf');
  });

  it('truncates rather than exceeding the cap', () => {
    const many = Array.from({ length: 40 }, (_, i) => file(`long-file-name-${i}.png`, 1));
    const out = attachmentFallbackMessage(many);
    expect(out.length).toBe(FALLBACK_MESSAGE_MAX);
    expect(out.endsWith('…')).toBe(true);
  });
});

describe('formatBytes / isImage', () => {
  it('formats KB below a megabyte and MB above', () => {
    expect(formatBytes(2048)).toBe('2 KB');
    expect(formatBytes(1024 * 1024)).toBe('1.0 MB');
  });

  it('recognizes image types case-insensitively', () => {
    expect(isImage('image/png')).toBe(true);
    expect(isImage('IMAGE/JPEG')).toBe(true);
    expect(isImage('application/pdf')).toBe(false);
    expect(isImage('')).toBe(false);
  });
});

describe('client caps mirror the server', () => {
  it('matches attachment_service.MAX_ATTACHMENT_BYTES and MAX_ATTACHMENTS_PER_NOTE', () => {
    // If these drift, the UI either refuses uploads the server would take or lets the user
    // stage files that come back 413 after the note has already been created.
    expect(MAX_ATTACHMENT_BYTES).toBe(10 * 1024 * 1024);
    expect(MAX_ATTACHMENTS).toBe(10);
  });

  it('previews only the types attachment_service.INLINE_IMAGE_MIMES keeps as images', () => {
    // SVG is the case worth naming: the browser calls it an image, the server stores it as
    // an inert octet-stream, so a preview would promise a picture the note cannot show.
    expect([...PREVIEWABLE_IMAGE_MIMES].sort())
      .toEqual(['image/gif', 'image/jpeg', 'image/png', 'image/webp']);
    expect(PREVIEWABLE_IMAGE_MIMES.has('image/svg+xml')).toBe(false);
    // ...and it is deliberately NARROWER than isImage(), which answers a different question.
    expect(isImage('image/svg+xml')).toBe(true);
  });
});
