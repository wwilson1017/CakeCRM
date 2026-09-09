// @vitest-environment jsdom
//
// The post-then-upload state machine (#57).
//
// The entity-switch guard is why this hook exists rather than three lines in NotesThread.
// Retry state names a note id on a SPECIFIC record, and the deal detail sheet does not
// remount between deals — so without it a failed upload on deal A leaves a live Retry
// button on deal B that files the photo onto A's note, silently, behind a success
// affordance.
import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useChatterPost, type ChatterPost } from './useChatterPost';

const file = (name: string): File => new File(['x'], name, { type: 'image/png' });

/** Drain the microtask queue so an in-flight promise chain reaches its next await. */
const flush = () => new Promise<void>(r => setTimeout(r, 0));

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

interface Harness {
  latest: () => ChatterPost;
  switchTo: (key: string) => void;
}

function render(
  createNote: (m: string) => Promise<{ id: number }>,
  onPosted: () => void,
  upload: (p: string, f: File) => Promise<unknown>,
  initialKey = 'deal:1',
): Harness {
  const seen: ChatterPost[] = [];
  let setKey: (k: string) => void = () => {};

  function Probe() {
    const [key, setK] = useState(initialKey);
    setKey = setK;
    seen.push(useChatterPost(key, createNote, onPosted, upload));
    return null;
  }

  act(() => { root.render(<Probe />); });
  return {
    latest: () => seen[seen.length - 1],
    switchTo: (k: string) => act(() => { setKey(k); }),
  };
}

describe('useChatterPost', () => {
  it('posts a note and reports nothing to retry when everything lands', async () => {
    const onPosted = vi.fn();
    const h = render(async () => ({ id: 5 }), onPosted, vi.fn());
    await act(async () => { await h.latest().post('hello', [file('a.png')]); });
    expect(h.latest().retryFiles).toBeUndefined();
    expect(h.latest().notice).toBeNull();
    expect(onPosted).toHaveBeenCalledTimes(1);
  });

  it('keeps the failed files staged, with a countable notice', async () => {
    const upload = vi.fn(async (_p: string, f: File) => {
      if (f.name !== 'ok.png') throw new Error('413');
    });
    const h = render(async () => ({ id: 5 }), vi.fn(), upload);
    await act(async () => {
      await h.latest().post('x', [file('ok.png'), file('bad.png'), file('worse.png')]);
    });
    expect(h.latest().retryFiles?.map(f => f.name)).toEqual(['bad.png', 'worse.png']);
    expect(h.latest().notice).toBe("2 attachments didn't upload.");
  });

  it('singularizes the notice for one file', async () => {
    const h = render(async () => ({ id: 5 }), vi.fn(),
                     async () => { throw new Error('nope'); });
    await act(async () => { await h.latest().post('x', [file('a.png')]); });
    expect(h.latest().notice).toBe("1 attachment didn't upload.");
  });

  it('retries against the note that already exists, not a new one', async () => {
    const createNote = vi.fn(async () => ({ id: 77 }));
    let fail = true;
    const paths: string[] = [];
    const upload = vi.fn(async (p: string) => {
      paths.push(p);
      if (fail) throw new Error('413');
    });
    const h = render(createNote, vi.fn(), upload);
    await act(async () => { await h.latest().post('x', [file('a.png')]); });
    expect(h.latest().retryFiles).toHaveLength(1);

    fail = false;
    await act(async () => { h.latest().retry(); });
    await act(async () => { await Promise.resolve(); });

    // One note, two upload attempts — both aimed at note 77.
    expect(createNote).toHaveBeenCalledTimes(1);
    expect(paths).toEqual([
      '/api/crm/chatter/note/77/attachments',
      '/api/crm/chatter/note/77/attachments',
    ]);
    expect(h.latest().retryFiles).toBeUndefined();
  });

  it('discardRetry clears the banner', async () => {
    const h = render(async () => ({ id: 5 }), vi.fn(),
                     async () => { throw new Error('nope'); });
    await act(async () => { await h.latest().post('x', [file('a.png')]); });
    act(() => { h.latest().discardRetry(); });
    expect(h.latest().retryFiles).toBeUndefined();
    expect(h.latest().notice).toBeNull();
  });

  it('rejects when the note itself fails, so the composer keeps the text', async () => {
    const h = render(async () => { throw new Error('boom'); }, vi.fn(), vi.fn());
    await expect(act(async () => { await h.latest().post('important', []); }))
      .rejects.toThrow('boom');
  });

  it('drops retry state the moment the panel points at a different record', async () => {
    const h = render(async () => ({ id: 5 }), vi.fn(),
                     async () => { throw new Error('413'); });
    await act(async () => { await h.latest().post('x', [file('a.png')]); });
    expect(h.latest().retryFiles).toHaveLength(1);

    h.switchTo('deal:2');
    // A Retry button here would file deal 1's photo onto deal 1's note while the user is
    // looking at deal 2.
    expect(h.latest().retryFiles).toBeUndefined();
    expect(h.latest().notice).toBeNull();
  });

  it('a post that resolves AFTER the switch lands no state on the new record', async () => {
    // The real window: an upload takes seconds and the sheet stays interactive, so this is
    // ordinary use rather than a rare race.
    let finish: () => void = () => {};
    const onPosted = vi.fn();
    const upload = vi.fn(() => new Promise<never>((_, rej) => {
      finish = () => rej(new Error('413'));
    }));
    const h = render(async () => ({ id: 5 }), onPosted, upload);

    let posting!: Promise<void>;
    // Let the note creation resolve so the upload is actually in flight — `finish` is only
    // assigned once the mock has been called.
    await act(async () => {
      posting = h.latest().post('x', [file('a.png')]);
      await flush();
    });
    expect(upload).toHaveBeenCalledTimes(1);

    h.switchTo('deal:2');
    await act(async () => { finish(); await posting; });

    expect(h.latest().retryFiles).toBeUndefined();
    // onPosted would refetch and paint deal 1's thread into deal 2's panel.
    expect(onPosted).not.toHaveBeenCalled();
  });

  it('a retry that resolves after the switch lands no state either', async () => {
    let fail = true;
    let finish: () => void = () => {};
    const onPosted = vi.fn();
    const upload = vi.fn(() => fail
      ? Promise.reject(new Error('413'))
      : new Promise<void>(res => { finish = res; }));
    const h = render(async () => ({ id: 5 }), onPosted, upload);
    await act(async () => { await h.latest().post('x', [file('a.png')]); });
    expect(h.latest().retryFiles).toHaveLength(1);
    onPosted.mockClear();

    fail = false;
    await act(async () => { h.latest().retry(); await flush(); });
    h.switchTo('deal:2');
    await act(async () => { finish(); await flush(); });

    expect(onPosted).not.toHaveBeenCalled();
    expect(h.latest().retryFiles).toBeUndefined();
  });
});
