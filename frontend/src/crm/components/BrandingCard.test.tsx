// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../core/api/client';
import { shrinkImage } from '../../shared/shrinkImage';
import { toast } from '../../shared/toast';
import { BrandingCard } from './BrandingCard';

vi.mock('../../core/api/client', () => ({ api: vi.fn() }));
vi.mock('../../shared/shrinkImage', () => ({ shrinkImage: vi.fn() }));
vi.mock('../../shared/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('../../core/branding/BrandingContext', () => ({
  useBranding: () => ({ branding: { company_name: 'Example', has_logo: false },
    loadError: null, patchBranding: vi.fn(), bumpLogoVersion: vi.fn(), logoVersion: 0 }),
}));
let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  vi.clearAllMocks();
  container = document.createElement('div');
  root = createRoot(container);
  act(() => root.render(<BrandingCard isMobile />));
});
afterEach(() => act(() => root.unmount()));

it.each([true, false])('checks the logo size after shrinking (success: %s) and clears busy', async success => {
  const original = new File([new Uint8Array(3 * 1024 * 1024)], 'logo.png', { type: 'image/png' });
  const shrunk = new File([new Uint8Array(200 * 1024)], 'logo.webp', { type: 'image/webp' });
  let finish!: (file: File) => void;
  vi.mocked(shrinkImage).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
  Object.defineProperty(input, 'files', { value: [original] });
  await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })));
  expect(input.disabled).toBe(true);
  expect(shrinkImage).toHaveBeenCalledWith(original);
  await act(async () => finish(success ? shrunk : original));
  expect(input.disabled).toBe(false);
  if (success) {
    expect(api).toHaveBeenCalledOnce();
    const [path, options] = vi.mocked(api).mock.calls[0];
    expect(path).toBe('/api/branding/logo');
    expect((options?.body as FormData).get('file')).toBe(shrunk);
  } else {
    expect(api).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith('Logo must be under 2 MB.');
  }
});
