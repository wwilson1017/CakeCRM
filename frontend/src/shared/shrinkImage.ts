// Browser-side photo shrink (#339): every image upload path
// (chatter attachments, the branding logo) sends a ~300 KB, 1600 px re-encode instead of
// a multi-MB phone photo. The canvas re-encode drops EXIF, which is what strips location.
// Never blocks an upload: anything it can't or shouldn't handle goes out as the original.
// No React and no crm/ imports, so any bundle can use it.

export const SHRINK_MAX_EDGE = 1600;
export const SHRINK_TARGET_BYTES = 300 * 1024;

interface DecodedImage {
  width: number;
  height: number;
  source: CanvasImageSource;
  close?: () => void;
}

export interface ShrinkDeps {
  decode: (file: File) => Promise<DecodedImage>;
  encode: (
    source: CanvasImageSource, width: number, height: number,
    type: string, quality: number, fillWhite: boolean,
  ) => Promise<Blob | null>;
}

export function fitWithin(width: number, height: number, max: number) {
  const scale = Math.min(1, max / Math.max(width, height));
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

const defaults: ShrinkDeps = {
  async decode(file) {
    const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
    return { width: bitmap.width, height: bitmap.height, source: bitmap, close: () => bitmap.close() };
  },
  async encode(source, width, height, type, quality, fillWhite) {
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Canvas unavailable');
    if (fillWhite) {
      ctx.fillStyle = '#fff';
      ctx.fillRect(0, 0, width, height);
    }
    ctx.drawImage(source, 0, 0, width, height);
    return new Promise(resolve => canvas.toBlob(resolve, type, quality));
  },
};

const ascii = (bytes: Uint8Array, at: number, text: string) =>
  [...text].every((c, i) => bytes[at + i] === c.charCodeAt(0));

/**
 * Animated WebP (VP8X animation flag) and APNG (an acTL chunk) would flatten to their first
 * frame through a canvas, so they pass through like GIFs. Both markers sit in the header.
 */
export async function isAnimated(file: File): Promise<boolean> {
  const bytes = new Uint8Array(await file.slice(0, 64 * 1024).arrayBuffer());
  if (file.type === 'image/webp') {
    return ascii(bytes, 12, 'VP8X') && (bytes[20] & 0x02) !== 0;
  }
  if (file.type === 'image/png') {
    // Chunks are length(4) type(4) data crc(4); acTL must precede the first IDAT.
    for (let at = 8; at + 8 <= bytes.length;) {
      if (ascii(bytes, at + 4, 'acTL')) return true;
      if (ascii(bytes, at + 4, 'IDAT')) return false;
      at += 12 + new DataView(bytes.buffer).getUint32(at);
    }
  }
  return false;
}

/** Best-effort shrinking: canvas output drops EXIF; any passthrough retains original bytes. */
export async function shrinkImage(file: File, deps: ShrinkDeps = defaults): Promise<File> {
  if (!file.type.startsWith('image/') || ['image/gif', 'image/svg+xml'].includes(file.type)) return file;
  let decoded: DecodedImage | undefined;
  try {
    if (await isAnimated(file)) return file;
    decoded = await deps.decode(file);
    if (!(decoded.width > 0 && decoded.height > 0)) return file;
    let { width, height } = fitWithin(decoded.width, decoded.height, SHRINK_MAX_EDGE);
    const type = ['image/png', 'image/webp'].includes(file.type) ? 'image/webp' : 'image/jpeg';
    let candidate: Blob | null = null;
    // Initial dimensions plus at most three reductions; keep the work bounded on phones.
    for (let step = 0; step <= 3; step++) {
      for (const quality of [0.85, 0.75, 0.65, 0.55]) {
        candidate = await deps.encode(decoded.source, width, height, type, quality, type === 'image/jpeg');
        if (!candidate || candidate.type !== type) return file;
        if (candidate.size <= SHRINK_TARGET_BYTES) break;
      }
      if (candidate!.size <= SHRINK_TARGET_BYTES) break;
      width = Math.max(1, Math.round(width * 0.75));
      height = Math.max(1, Math.round(height * 0.75));
    }
    const stripLocation = ['image/jpeg', 'image/heic', 'image/heif'].includes(file.type);
    if (!candidate || (candidate.size > file.size && !stripLocation)) return file;
    const base = file.name.replace(/\.[^.]+$/, '');
    return new File([candidate], `${base}.${type === 'image/webp' ? 'webp' : 'jpg'}`, {
      type, lastModified: file.lastModified,
    });
  } catch {
    return file;
  } finally {
    decoded?.close?.();
  }
}
