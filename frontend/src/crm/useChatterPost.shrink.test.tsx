// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { api } from '../core/api/client';
import { shrinkImage } from '../shared/shrinkImage';
import { useChatterPost, type ChatterPost } from './useChatterPost';

vi.mock('../core/api/client', () => ({ api: vi.fn() }));
vi.mock('../shared/shrinkImage', () => ({ shrinkImage: vi.fn() }));

it('uploads the shrunk file and re-shrinks the original on Retry', async () => {
  const original = new File(['original'], 'phone.jpg', { type: 'image/jpeg' });
  const shrunk = new File(['small'], 'phone.jpg', { type: 'image/jpeg' });
  vi.mocked(shrinkImage).mockResolvedValue(shrunk);
  vi.mocked(api).mockRejectedValueOnce(new Error('retry')).mockResolvedValue({});
  let latest!: ChatterPost;
  function Probe() {
    latest = useChatterPost('contact:1', async () => ({ id: 7 }), vi.fn());
    return null;
  }
  const container = document.createElement('div');
  const root = createRoot(container);
  try {
    act(() => root.render(<Probe />));
    await act(async () => latest.post('photo', [original]));
    expect(latest.retryFiles).toEqual([original]);
    await act(async () => { latest.retry(); });
    expect(shrinkImage).toHaveBeenNthCalledWith(2, original);
    expect(api).toHaveBeenCalledTimes(2);
    for (const [path, options] of vi.mocked(api).mock.calls) {
      expect(path).toBe('/api/crm/chatter/note/7/attachments');
      expect((options?.body as FormData).get('file')).toBe(shrunk);
    }
    expect(latest.retryFiles).toBeUndefined();
  } finally {
    act(() => root.unmount());
  }
});
