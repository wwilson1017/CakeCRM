// @vitest-environment jsdom
//
// The composer's selective clear (#57) — the one piece of non-trivial stateful logic here.
//
// Uploading several photos takes seconds and the textarea stays live throughout, so submit
// must clear ONLY what it actually sent. A blanket `setStaged([])` would wipe a file pasted
// during that window without ever uploading it, and leak its object URL besides (the
// unmount cleanup reads a ref the blanket reset had already emptied). The code comment
// claims to fix exactly that; this file is what stops the natural "simplify it" refactor
// from silently un-fixing it.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NoteComposer } from './NoteComposer';

let container: HTMLDivElement;
let root: Root;
let created: string[];
let revoked: string[];
let seq: number;

beforeEach(() => {
  created = [];
  revoked = [];
  seq = 0;
  URL.createObjectURL = vi.fn(() => {
    const url = `blob:mock/${++seq}`;
    created.push(url);
    return url;
  });
  URL.revokeObjectURL = vi.fn((u: string) => { revoked.push(u); });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const png = (name: string): File =>
  new File([new Uint8Array([1, 2, 3])], name, { type: 'image/png' });

function render(onSubmit: (t: string, f: File[]) => Promise<void>) {
  act(() => { root.render(<NoteComposer onSubmit={onSubmit} />); });
}

const textarea = () => container.querySelector('textarea')!;
const postButton = () =>
  Array.from(container.querySelectorAll('button')).find(b => /Post|Posting/.test(b.textContent!))!;
const chips = () => Array.from(container.querySelectorAll('button'))
  .filter(b => b.getAttribute('aria-label')?.startsWith('Remove '))
  .map(b => b.getAttribute('aria-label')!.replace('Remove ', ''));

/** Drop files onto the composer — the intake path that needs no file dialog. */
function drop(files: File[]) {
  act(() => {
    const e = new Event('drop', { bubbles: true, cancelable: true });
    Object.defineProperty(e, 'dataTransfer', {
      value: { items: files.map(f => ({ kind: 'file', getAsFile: () => f })), files: [] },
    });
    container.firstElementChild!.dispatchEvent(e);
  });
}

function type(value: string) {
  act(() => {
    const el = textarea();
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype, 'value',
    )!.set!;
    setter.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

describe('NoteComposer — submit clears only what it sent', () => {
  it('keeps a file staged if it arrived while the upload was in flight', async () => {
    let release: () => void = () => {};
    const sent: File[][] = [];
    render((_t, f) => {
      sent.push(f);
      return new Promise<void>(res => { release = res; });
    });

    drop([png('first.png')]);
    expect(chips()).toEqual(['first.png']);

    act(() => { postButton().dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    // ...and the user pastes another one before the upload finishes.
    drop([png('second.png')]);
    expect(chips()).toEqual(['first.png', 'second.png']);

    await act(async () => { release(); await Promise.resolve(); });

    // Only the submitted file left; the mid-flight one is still there to be posted.
    expect(chips()).toEqual(['second.png']);
    expect(sent[0].map(f => f.name)).toEqual(['first.png']);
  });

  it('revokes ONLY the submitted file preview, not the one still staged', async () => {
    let release: () => void = () => {};
    render(() => new Promise<void>(res => { release = res; }));

    drop([png('first.png')]);
    act(() => { postButton().dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    drop([png('second.png')]);
    await act(async () => { release(); await Promise.resolve(); });

    // A blanket clear would revoke both — and the survivor's chip would render a dead URL.
    expect(created).toEqual(['blob:mock/1', 'blob:mock/2']);
    expect(revoked).toEqual(['blob:mock/1']);
  });

  it('keeps text typed during the upload instead of wiping it', async () => {
    let release: () => void = () => {};
    render(() => new Promise<void>(res => { release = res; }));

    type('first thought');
    act(() => { postButton().dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    type('first thought, plus more');
    await act(async () => { release(); await Promise.resolve(); });

    expect(textarea().value).toBe('first thought, plus more');
  });

  it('clears the text when nothing was typed during the upload', async () => {
    render(async () => {});
    type('all of it');
    await act(async () => {
      postButton().dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
    });
    expect(textarea().value).toBe('');
  });

  it('keeps everything staged when the post fails', async () => {
    render(async () => { throw new Error('server said no'); });
    type('a note');
    drop([png('keep.png')]);
    await act(async () => {
      postButton().dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
    });

    expect(textarea().value).toBe('a note');
    expect(chips()).toEqual(['keep.png']);
    expect(revoked).toEqual([]);
    expect(container.textContent).toContain('server said no');
  });

  it('revokes every surviving preview on unmount', async () => {
    render(async () => {});
    drop([png('a.png'), png('b.png')]);
    act(() => root.unmount());
    root = createRoot(container);
    expect([...revoked].sort()).toEqual([...created].sort());
  });
});

describe('NoteComposer — intake', () => {
  it('will not post an empty note', async () => {
    const onSubmit = vi.fn(async () => {});
    render(onSubmit);
    act(() => { postButton().dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('posts a files-only note', async () => {
    const sent: File[][] = [];
    render(async (_t, f) => { sent.push(f); });
    drop([png('only.png')]);
    await act(async () => {
      postButton().dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
    });
    expect(sent).toHaveLength(1);
    expect(sent[0].map(f => f.name)).toEqual(['only.png']);
  });

  it('surfaces a rejected file instead of dropping it silently', () => {
    render(async () => {});
    const folder = new File([], 'Photos', { type: '' });
    drop([folder]);
    expect(chips()).toEqual([]);
    expect(container.textContent).toContain("Folders can't be attached");
  });

  it('previews an image but not a type the server stores as octet-stream', () => {
    render(async () => {});
    drop([png('shot.png'), new File(['<svg/>'], 'logo.svg', { type: 'image/svg+xml' })]);
    expect(chips()).toEqual(['shot.png', 'logo.svg']);
    // Only the PNG got a preview URL — an SVG chip would promise a picture the posted note
    // cannot show, since the server stores it inert.
    expect(created).toHaveLength(1);
    expect(container.querySelectorAll('img')).toHaveLength(1);
  });
});
