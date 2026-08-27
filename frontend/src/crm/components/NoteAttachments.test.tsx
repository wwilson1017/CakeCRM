// @vitest-environment jsdom
//
// The has_thumb gate (#57) — the one behavior in this component that is a safety rule
// rather than a layout choice.
//
// A NULL thumbnail on an image means the server's decompression-bomb ceilings REFUSED to
// decode those bytes. Offering to open it in a lightbox would hand the browser exactly the
// decode the server declined, so such an attachment must download instead. AttachmentLightbox's
// docstring names this invariant; this file is what makes that claim true.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CrmAttachment } from '../../core/types';

const api = vi.fn();
const apiBlob = vi.fn();
vi.mock('../../core/api/client', () => ({
  api: (...a: unknown[]) => api(...a),
  apiBlob: (...a: unknown[]) => apiBlob(...a),
}));
const toastError = vi.fn();
vi.mock('../../shared/toast', () => ({ toast: { error: (m: string) => toastError(m) } }));

const { NoteAttachments } = await import('./NoteAttachments');

const attachment = (over: Partial<CrmAttachment> = {}): CrmAttachment => ({
  id: 1, note_id: 9, filename: 'photo.png', mime_type: 'image/png',
  byte_size: 2048, has_thumb: true, created_at: '2026-08-27T00:00:00Z',
  uploaded_by: 1, ...over,
});

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  api.mockReset();
  apiBlob.mockReset();
  toastError.mockReset();
  apiBlob.mockResolvedValue(new Blob(['x']));
  URL.createObjectURL = vi.fn(() => 'blob:mock/1');
  URL.revokeObjectURL = vi.fn();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(items: CrmAttachment[], onChanged = vi.fn()) {
  act(() => { root.render(<NoteAttachments items={items} onChanged={onChanged} />); });
  return onChanged;
}

/** Every button except the per-item "Remove" control. */
const primaryButtons = () =>
  Array.from(container.querySelectorAll('button')).filter(b => b.textContent !== 'Remove');

const click = (el: Element) =>
  act(() => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })); });

const settle = async () => { await act(async () => { await Promise.resolve(); }); };

describe('NoteAttachments — the has_thumb gate', () => {
  it('an image WITH a thumbnail opens the lightbox', async () => {
    render([attachment()]);
    await settle();
    click(primaryButtons()[0]);
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();
  });

  it('an image WITHOUT a thumbnail downloads instead of opening', async () => {
    // The server refused to decode these bytes; the browser must not be asked to.
    render([attachment({ has_thumb: false })]);
    await settle();
    click(primaryButtons()[0]);
    await settle();
    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(apiBlob).toHaveBeenCalledWith('/api/crm/chatter/attachments/1/file');
  });

  it('a non-image downloads even when a thumbnail somehow exists', async () => {
    render([attachment({ mime_type: 'application/pdf', filename: 'quote.pdf' })]);
    await settle();
    click(primaryButtons()[0]);
    await settle();
    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(apiBlob).toHaveBeenCalled();
  });

  it('fetches the THUMBNAIL for a tile, never the original', async () => {
    // The "bounded image memory" rule: a list view must not pull full-size bytes.
    render([attachment()]);
    await settle();
    expect(apiBlob).toHaveBeenCalledWith('/api/crm/chatter/attachments/1/thumb');
    expect(apiBlob).not.toHaveBeenCalledWith('/api/crm/chatter/attachments/1/file');
  });
});

describe('NoteAttachments — removal', () => {
  it('confirms before deleting, then reports the change', async () => {
    window.confirm = vi.fn(() => true);
    api.mockResolvedValue({ ok: true });
    const onChanged = render([attachment()]);
    await settle();

    const remove = Array.from(container.querySelectorAll('button'))
      .find(b => b.textContent === 'Remove')!;
    click(remove);
    await settle();

    expect(window.confirm).toHaveBeenCalled();
    expect(api).toHaveBeenCalledWith('/api/crm/chatter/attachments/1', { method: 'DELETE' });
    expect(onChanged).toHaveBeenCalled();
  });

  it('declining the confirm deletes nothing', async () => {
    window.confirm = vi.fn(() => false);
    const onChanged = render([attachment()]);
    await settle();

    click(Array.from(container.querySelectorAll('button')).find(b => b.textContent === 'Remove')!);
    await settle();

    expect(api).not.toHaveBeenCalled();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it('surfaces a failed delete instead of silently claiming success', async () => {
    window.confirm = vi.fn(() => true);
    api.mockRejectedValue(new Error('500'));
    const onChanged = render([attachment()]);
    await settle();

    click(Array.from(container.querySelectorAll('button')).find(b => b.textContent === 'Remove')!);
    await settle();

    expect(toastError).toHaveBeenCalled();
    // No refetch — nothing changed, and pretending otherwise would make the row vanish.
    expect(onChanged).not.toHaveBeenCalled();
  });
});
