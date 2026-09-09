// The post-then-upload sequence (#57). Pure — no DOM needed.
//
// The ordering claim is the whole point: the note is created FIRST, and a failed upload
// comes back WITH the id of the note that now exists, so the composer can offer Retry
// against something real instead of losing the file.
import { describe, expect, it, vi } from 'vitest';
import { postNoteWithAttachments, uploadAttachments } from './postNote';

const file = (name: string): File => new File(['x'], name, { type: 'image/png' });
const path = (id: number) => `/api/crm/chatter/note/${id}/attachments`;

describe('postNoteWithAttachments', () => {
  it('creates the note, then uploads each file to it', async () => {
    const order: string[] = [];
    const createNote = vi.fn(async (m: string) => { order.push(`note:${m}`); return { id: 42 }; });
    const upload = vi.fn(async (p: string, f: File) => { order.push(`up:${f.name}:${p}`); });

    const out = await postNoteWithAttachments({
      text: 'Sent the revised quote.', files: [file('a.png'), file('b.png')],
      createNote, uploadPath: path, upload,
    });

    expect(out).toEqual({ noteId: 42, failures: [] });
    expect(order).toEqual([
      'note:Sent the revised quote.',
      'up:a.png:/api/crm/chatter/note/42/attachments',
      'up:b.png:/api/crm/chatter/note/42/attachments',
    ]);
  });

  it('trims the typed text before sending it', async () => {
    const createNote = vi.fn(async () => ({ id: 1 }));
    await postNoteWithAttachments({
      text: '  spaced  ', files: [], createNote, uploadPath: path, upload: vi.fn(),
    });
    expect(createNote).toHaveBeenCalledWith('spaced');
  });

  it('generates a body for a files-only post', async () => {
    // add_note rejects an empty message, and widening that server-side would be a much
    // wider blast radius than naming the files here.
    const createNote = vi.fn(async () => ({ id: 7 }));
    await postNoteWithAttachments({
      text: '   ', files: [file('receipt.pdf')], createNote, uploadPath: path, upload: vi.fn(),
    });
    expect(createNote).toHaveBeenCalledWith('Attached: receipt.pdf');
  });

  it('skips uploading entirely when there are no files', async () => {
    const upload = vi.fn();
    const out = await postNoteWithAttachments({
      text: 'just words', files: [], createNote: async () => ({ id: 3 }),
      uploadPath: path, upload,
    });
    expect(upload).not.toHaveBeenCalled();
    expect(out).toEqual({ noteId: 3, failures: [] });
  });

  it('rejects when the note itself fails, so the composer keeps the text', async () => {
    const upload = vi.fn();
    await expect(postNoteWithAttachments({
      text: 'important', files: [file('a.png')],
      createNote: async () => { throw new Error('boom'); },
      uploadPath: path, upload,
    })).rejects.toThrow('boom');
    expect(upload).not.toHaveBeenCalled();
  });

  it('returns per-file failures WITH the note id instead of throwing', async () => {
    // Without the id there is nothing for Retry to aim at, and "attach to an existing
    // note" is the only recovery left.
    const bad = file('b.png');
    const upload = vi.fn(async (_p: string, f: File) => {
      if (f.name === 'b.png') throw new Error('Upload failed: 413');
    });
    const out = await postNoteWithAttachments({
      text: 'x', files: [file('a.png'), bad, file('c.png')],
      createNote: async () => ({ id: 9 }), uploadPath: path, upload,
    });
    expect(out.noteId).toBe(9);
    expect(out.failures).toEqual([{ file: bad, error: 'Upload failed: 413' }]);
    // A failure part-way through does not abandon the rest.
    expect(upload).toHaveBeenCalledTimes(3);
  });
});

describe('uploadAttachments', () => {
  it('uploads sequentially and collects every failure', async () => {
    const inFlight: number[] = [];
    let live = 0;
    const upload = vi.fn(async () => {
      live += 1;
      inFlight.push(live);
      await Promise.resolve();
      live -= 1;
    });
    await uploadAttachments(5, [file('a.png'), file('b.png'), file('c.png')], path, upload);
    // Sequential, not parallel: the server serializes per note anyway (every upload locks
    // the parent row), and a mid-batch failure leaves an obvious "these N are staged".
    expect(inFlight).toEqual([1, 1, 1]);
  });

  it('describes a non-Error rejection rather than rendering undefined', async () => {
    const f = file('a.png');
    const out = await uploadAttachments(5, [f], path, async () => { throw 'nope'; });
    expect(out).toEqual([{ file: f, error: 'Upload failed' }]);
  });

  it('returns [] when everything lands', async () => {
    expect(await uploadAttachments(5, [file('a.png')], path, vi.fn())).toEqual([]);
  });
});
