// @vitest-environment jsdom
//
// The lightbox (#57): dismissal semantics, and the fact that this is the ONLY place the
// full-size original is ever fetched. A list view that pulled originals is the exact
// "bounded image memory" failure the issue names, so "opening is what triggers the fetch"
// is a load-bearing property, not an implementation detail.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CrmAttachment } from '../../core/types';

const apiBlob = vi.fn();
vi.mock('../../core/api/client', () => ({
  apiBlob: (p: string, signal?: AbortSignal) => apiBlob(p, signal),
}));

const { AttachmentLightbox } = await import('./AttachmentLightbox');

const ATTACHMENT: CrmAttachment = {
  id: 4, note_id: 9, filename: 'pallet.png', mime_type: 'image/png',
  byte_size: 4096, has_thumb: true, created_at: '2026-08-27T00:00:00Z', uploaded_by: 1,
};

let container: HTMLDivElement;
let root: Root;
let revoked: string[];

beforeEach(() => {
  apiBlob.mockReset();
  apiBlob.mockResolvedValue(new Blob(['x']));
  revoked = [];
  URL.createObjectURL = vi.fn(() => 'blob:mock/1');
  URL.revokeObjectURL = vi.fn((u: string) => { revoked.push(u); });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(onClose = vi.fn()) {
  act(() => { root.render(<AttachmentLightbox attachment={ATTACHMENT} onClose={onClose} />); });
  return onClose;
}

const settle = async () => { await act(async () => { await Promise.resolve(); }); };
const dialog = () => container.querySelector('[role="dialog"]')!;

describe('AttachmentLightbox', () => {
  it('fetches the ORIGINAL — this is the only place that does', async () => {
    render();
    await settle();
    expect(apiBlob.mock.calls[0][0]).toBe('/api/crm/chatter/attachments/4/file');
  });

  it('renders the image once the bytes arrive, labelled by filename', async () => {
    render();
    await settle();
    const img = container.querySelector('img')!;
    expect(img.getAttribute('src')).toBe('blob:mock/1');
    expect(img.getAttribute('alt')).toBe('pallet.png');
  });

  it('closes on Escape', async () => {
    const onClose = render();
    await settle();
    act(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('claims Escape in the CAPTURE phase so a modal underneath does not also close', async () => {
    // The contract shared/overlay/DetailModal.tsx documents for this component role: it
    // listens in BUBBLE and defers to defaultPrevented, so a lightbox over a modal must
    // take the key in capture — or one Escape dismisses both the picture and the sheet
    // behind it. Latent until #77 puts detail surfaces on CollectionDetail; pinned now.
    const modalSaw: KeyboardEvent[] = [];
    const modalListener = (e: Event) => modalSaw.push(e as KeyboardEvent);
    document.addEventListener('keydown', modalListener);       // bubble, like DetailModal
    try {
      const onClose = render();
      await settle();
      act(() => {
        document.dispatchEvent(new KeyboardEvent('keydown', {
          key: 'Escape', bubbles: true, cancelable: true,
        }));
      });
      expect(onClose).toHaveBeenCalledTimes(1);
      // The modal's own listener either never runs, or sees the key already claimed.
      expect(modalSaw.every(e => e.defaultPrevented)).toBe(true);
    } finally {
      document.removeEventListener('keydown', modalListener);
    }
  });

  it('ignores other keys', async () => {
    const onClose = render();
    await settle();
    act(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', bubbles: true }));
    });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('closes on a backdrop click', async () => {
    const onClose = render();
    await settle();
    act(() => { dialog().dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does NOT close when the image itself is clicked', async () => {
    // Without stopPropagation, a mis-click or a drag-to-select on the picture would
    // dismiss what the user just opened.
    const onClose = render();
    await settle();
    act(() => {
      container.querySelector('img')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('stops listening for Escape once closed', async () => {
    const onClose = render();
    await settle();
    act(() => root.unmount());
    root = createRoot(container);
    act(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('releases the original bytes on close', async () => {
    // The whole point of fetching originals lazily is undone if they are never freed.
    render();
    await settle();
    act(() => root.unmount());
    root = createRoot(container);
    expect(revoked).toEqual(['blob:mock/1']);
  });

  it('says so rather than hanging when the fetch fails', async () => {
    apiBlob.mockRejectedValue(new Error('404'));
    render();
    await settle();
    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toContain("Couldn't load");
  });
});
