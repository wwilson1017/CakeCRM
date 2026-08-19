import type { CSSProperties } from 'react';

// Shared style tokens for the CRM UI. Every value resolves through a `--color-ck-*`
// custom property defined in index.css, which is imported by main.tsx before anything
// renders — so these carry NO literal fallbacks. That is what makes the `.dark`
// override block in index.css re-theme the whole app from one place: a fallback hex
// here would silently pin a light-theme colour into a dark surface.
// The exported NAMES are unchanged from the chatty port so consumers need no edits.

// ── Colors ───────────────────────────────────────────────────────────────────

export const INK = 'var(--color-ck-ink)';
export const INK_MUTE = 'var(--color-ck-ink-mute)';
export const INK_SOFT = 'var(--color-ck-ink-soft)';
export const INK_DIM = 'var(--color-ck-ink-dim)';
export const LINE = 'var(--color-ck-line)';
export const LINE_STRONG = 'var(--color-ck-line-strong)';
export const BG_ELEV = 'var(--color-ck-card)';
export const BG_CARD = 'var(--color-ck-card)';
export const BG_RAISED = 'var(--color-ck-raised)';
export const ACCENT = 'var(--color-ck-accent)';
export const ACCENT_INK = 'var(--color-ck-accent-ink)';
export const ACCENT_SOFT = 'var(--color-ck-accent-soft)';
// Accent as *text*/icon. Same value as ACCENT in light; lighter under .dark, where
// the fixed brand red fails WCAG AA on a dark surface. Fills keep ACCENT.
export const ACCENT_TEXT = 'var(--color-ck-accent-text)';
export const GOLD = 'var(--color-ck-amber)';
export const CORAL = 'var(--color-ck-red)';
export const SAGE = 'var(--color-ck-green)';
// Informational blue for assistant-authored surfaces (provenance, AI touch counts).
// Deliberately NOT the accent — these badges mean "an AI wrote this", not "brand".
export const AI = 'var(--color-ck-ai)';

// ── Chrome ───────────────────────────────────────────────────────────────────
// Declared per theme in index.css rather than derived here: HOVER is an *ink* tint,
// so a fixed dark tint would vanish on a dark surface.

export const HOVER = 'var(--color-ck-hover)';
export const SCRIM = 'var(--color-ck-scrim)';
export const SHADOW = 'var(--color-ck-shadow)';

/**
 * A translucent tint of a themed colour, for badge/chip backgrounds.
 * `color-mix` keeps the tint derived from the token, so it follows the `.dark`
 * override instead of freezing a light-theme rgba() into the source.
 */
export const tint = (color: string, pct: number): string =>
  `color-mix(in srgb, ${color} ${pct}%, transparent)`;

// ── Font stacks ──────────────────────────────────────────────────────────────

export const FONT_DISPLAY = "'Montserrat', 'Helvetica Neue', Helvetica, Arial, sans-serif";
export const FONT_SANS = "'Open Sans', 'Helvetica Neue', Helvetica, Arial, sans-serif";
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
