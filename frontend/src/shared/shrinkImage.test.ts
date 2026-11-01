import { afterEach, describe, expect, it, vi } from 'vitest';
import { fitWithin, shrinkImage, SHRINK_TARGET_BYTES, type ShrinkDeps } from './shrinkImage';

const file = (type = 'image/jpeg', size = 500_000) =>
  new File([new Uint8Array(size)], 'phone.photo.jpeg', { type, lastModified: 123 });
const blob = (size = 200_000, type = 'image/jpeg') => new Blob([new Uint8Array(size)], { type });
function harness(width = 4000, height = 3000) {
  const close = vi.fn();
  const decode = vi.fn(async () => ({ width, height, source: {} as CanvasImageSource, close }));
  const encode = vi.fn<ShrinkDeps['encode']>(async () => blob());
  return { decode, encode, close };
}
afterEach(() => vi.unstubAllGlobals());

describe('shrinkImage', () => {
  it.each([[4000, 3000, 1600, 1200], [3000, 4000, 1200, 1600], [40, 30, 40, 30]])(
    'fits %i × %i without upscaling', async (w, h, expectedW, expectedH) => {
      const deps = harness(w, h);
      await shrinkImage(file(), deps);
      expect(fitWithin(w, h, 1600)).toEqual({ width: expectedW, height: expectedH });
      expect(deps.encode).toHaveBeenCalledWith(expect.anything(), expectedW, expectedH, 'image/jpeg', 0.85, true);
      expect(deps.close).toHaveBeenCalledOnce();
    },
  );
  it('steps through quality then dimensions and stops at the target', async () => {
    const deps = harness();
    deps.encode.mockResolvedValue(blob(SHRINK_TARGET_BYTES + 1));
    deps.encode.mockResolvedValueOnce(blob(400_000)).mockResolvedValueOnce(blob(390_000))
      .mockResolvedValueOnce(blob(380_000)).mockResolvedValueOnce(blob(370_000))
      .mockResolvedValueOnce(blob(SHRINK_TARGET_BYTES));
    const result = await shrinkImage(file(), deps);
    expect(result.size).toBe(SHRINK_TARGET_BYTES);
    expect(deps.encode.mock.calls.map(c => [c[1], c[2], c[4]])).toEqual([
      [1600, 1200, 0.85], [1600, 1200, 0.75], [1600, 1200, 0.65], [1600, 1200, 0.55],
      [1200, 900, 0.85],
    ]);
  });
  it('bounds work to three dimension reductions even if still oversized', async () => {
    const deps = harness();
    deps.encode.mockResolvedValue(blob(350_000));
    expect((await shrinkImage(file(), deps)).size).toBe(350_000);
    expect(deps.encode).toHaveBeenCalledTimes(16);
    expect(deps.encode.mock.calls.at(-1)?.slice(1, 3)).toEqual([675, 506]);
  });
  it.each(['image/png', 'image/webp', 'image/jpeg', 'image/heic', 'image/heif'])(
    'chooses the format, filename, alpha and metadata policy for %s', async type => {
      const deps = harness();
      const webp = ['image/png', 'image/webp'].includes(type);
      deps.encode.mockResolvedValue(blob(200, webp ? 'image/webp' : 'image/jpeg'));
      const result = await shrinkImage(file(type), deps);
      expect(result.name).toBe(`phone.photo.${webp ? 'webp' : 'jpg'}`);
      expect(result.lastModified).toBe(123);
      expect(deps.encode.mock.calls[0].slice(3)).toEqual([webp ? 'image/webp' : 'image/jpeg', 0.85, !webp]);
    },
  );
  it.each(['image/png', 'image/jpeg'])('only grows EXIF camera formats: %s', async type => {
    const original = file(type, 10);
    const deps = harness();
    deps.encode.mockResolvedValue(blob(100, type === 'image/png' ? 'image/webp' : type));
    const result = await shrinkImage(original, deps);
    expect(result === original).toBe(type === 'image/png');
  });
  it.each(['application/pdf', 'text/plain', 'image/gif', 'image/svg+xml'])('passes %s through without decoding', async type => {
    const original = file(type);
    const deps = harness();
    expect(await shrinkImage(original, deps)).toBe(original);
    expect(deps.decode).not.toHaveBeenCalled();
  });
  it('passes through decoding failure', async () => {
    const original = file();
    const deps = harness();
    deps.decode.mockRejectedValue(new Error('unsupported'));
    expect(await shrinkImage(original, deps)).toBe(original);
  });
  it.each(['null', 'wrong type', 'throw'])('passes through encoder failure: %s and closes the bitmap', async failure => {
    const original = file();
    const deps = harness();
    if (failure === 'throw') deps.encode.mockRejectedValue(new Error('encoder'));
    else deps.encode.mockResolvedValue(failure === 'null' ? null : blob(100, 'image/png'));
    expect(await shrinkImage(original, deps)).toBe(original);
    expect(deps.close).toHaveBeenCalledOnce();
  });
  it('asks the default decoder to apply EXIF orientation', async () => {
    const close = vi.fn();
    const decode = vi.fn().mockResolvedValue({ width: 3000, height: 4000, close });
    vi.stubGlobal('createImageBitmap', decode);
    const original = file();
    // Node has no canvas: decoding succeeds, encoding falls back to the original.
    expect(await shrinkImage(original)).toBe(original);
    expect(decode).toHaveBeenCalledWith(original, { imageOrientation: 'from-image' });
    expect(close).toHaveBeenCalledOnce();
  });
});
