import type { CSSProperties } from 'react';

// Shared style tokens for the CRM UI. Ported from chatty's shared/styles.ts but
// remapped onto CakeCRM's warm-light `--color-ck-*` theme (see index.css). The
// exported NAMES are unchanged so the ported CRM pages/components need no edits.

// ── Colors (CSS custom property strings with fallbacks) ──────────────────────

export const INK = 'var(--color-ck-ink, #1F2328)';
export const INK_MUTE = 'var(--color-ck-ink-mute, #5A6069)';
export const INK_SOFT = 'var(--color-ck-ink-soft, #878D96)';
export const INK_DIM = 'var(--color-ck-ink-dim, #9AA0A8)';
export const LINE = 'var(--color-ck-line, #E7E4DF)';
export const LINE_STRONG = 'var(--color-ck-line-strong, #D8D4CD)';
export const BG_ELEV = 'var(--color-ck-card, #FFFFFF)';
export const BG_CARD = 'var(--color-ck-card, #FFFFFF)';
export const BG_RAISED = 'var(--color-ck-raised, #F3F1EE)';
export const ACCENT = 'var(--color-ck-accent, #B03A52)';
export const ACCENT_INK = 'var(--color-ck-accent-ink, #FFFFFF)';
export const ACCENT_SOFT = 'var(--color-ck-accent-soft, rgba(176, 58, 82, 0.09))';
export const GOLD = 'var(--color-ck-amber, #B07C2E)';
export const GOLD_HEX = '#B07C2E';
export const CORAL = 'var(--color-ck-red, #C24141)';
export const SAGE = 'var(--color-ck-green, #2E7D4F)';
export const SAGE_HEX = '#2E7D4F';

// ── Font stacks ──────────────────────────────────────────────────────────────

export const FONT_DISPLAY = "'Fraunces', Georgia, serif";
export const FONT_SANS = "'Inter', system-ui, sans-serif";
export const FONT_MONO = "ui-monospace, 'SF Mono', Menlo, monospace";

// ── Typography helper ────────────────────────────────────────────────────────

export const mono = (size: number, color: string = INK_DIM): CSSProperties => ({
  fontFamily: FONT_MONO,
  fontSize: size,
  letterSpacing: '0.16em',
  textTransform: 'uppercase',
  color,
});

// ── Form styles ──────────────────────────────────────────────────────────────

export const labelStyle: CSSProperties = {
  display: 'block',
  fontFamily: FONT_MONO,
  fontSize: 10, letterSpacing: '0.16em', textTransform: 'uppercase',
  color: INK_DIM, marginBottom: 6,
};

export const inputStyle: CSSProperties = {
  width: '100%', boxSizing: 'border-box',
  background: BG_RAISED, border: `1px solid ${LINE_STRONG}`,
  color: INK, borderRadius: 4, padding: '8px 12px', fontSize: 14, outline: 'none',
  fontFamily: FONT_SANS,
};

// ── Utilities ────────────────────────────────────────────────────────────────

export function formatNumber(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(n >= 10_000 ? 0 : 1) + 'K';
  return n.toLocaleString();
}
