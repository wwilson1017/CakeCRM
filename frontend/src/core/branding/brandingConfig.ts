/**
 * CakeCRM — Branding config types + accent helpers (no React).
 *
 * index.css routes the whole theme's accent through the `--brand-color` /
 * `--brand-color-soft` CSS custom properties; these helpers set/clear them and
 * strictly validate hex so a malformed accent can never poison the app-wide
 * `--color-ck-accent`.
 */

export interface BrandingConfig {
  company_name: string;
  accent_color: string;
  has_logo: boolean;
}

export const DEFAULT_BRANDING: BrandingConfig = {
  company_name: 'CakeCRM',
  accent_color: '#B03A52',
  has_logo: false,
};

const DEFAULT_SOFT = 'rgba(176, 58, 82, 0.09)';
const HEX_RE = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i;

/** Return `hex` iff it is a valid #rgb / #rrggbb color, else the brand default. */
export function normalizeHex(hex: string | null | undefined): string {
  return hex && HEX_RE.test(hex) ? hex : DEFAULT_BRANDING.accent_color;
}

/** Derive the soft accent (rgba at 0.09 alpha) from a hex color; never throws. */
export function hexToSoft(hex: string | null | undefined): string {
  if (!hex || !HEX_RE.test(hex)) return DEFAULT_SOFT;
  let h = hex.slice(1);
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, 0.09)`;
}

/** Expand #rgb → #rrggbb (native <input type=color> needs 6 digits); default on invalid. */
export function toSixDigitHex(hex: string): string {
  const h = normalizeHex(hex);
  if (h.length === 4) return '#' + h[1] + h[1] + h[2] + h[2] + h[3] + h[3];
  return h;
}

export function applyAccentVars(accent: string) {
  const root = document.documentElement;
  root.style.setProperty('--brand-color', normalizeHex(accent));
  root.style.setProperty('--brand-color-soft', hexToSoft(accent));
}

export function clearAccentVars() {
  const root = document.documentElement;
  root.style.removeProperty('--brand-color');
  root.style.removeProperty('--brand-color-soft');
}
