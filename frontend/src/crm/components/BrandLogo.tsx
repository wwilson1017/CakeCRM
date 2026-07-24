/**
 * BrandLogo — an <img> for the branding logo that falls back gracefully if the
 * image fails to load (e.g. the logo was deleted from another tab/device while
 * this tab's state still says has_logo). Honors the project's "never errors,
 * always degrades" rule instead of rendering a broken-image icon.
 *
 * Key this component on the logo version (`<BrandLogo key={logoVersion} .../>`)
 * so a new upload remounts it and clears any prior broken state — no effect.
 */

import { useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';

export function BrandLogo({ src, alt, style, fallback }: {
  src: string; alt: string; style?: CSSProperties; fallback: ReactNode;
}) {
  const [broken, setBroken] = useState(false);
  if (broken) return <>{fallback}</>;
  return <img src={src} alt={alt} style={style} onError={() => setBroken(true)} />;
}
