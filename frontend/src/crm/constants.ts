import { tint, GOLD_FILL, GOLD_TEXT, SAGE_FILL, SAGE_TEXT, AI_FILL, AI_TEXT, INK_DIM } from '../shared/styles';

export const STAGE_ORDER = ['lead', 'qualified', 'proposal', 'negotiation', 'won', 'lost'];

// Open (non-terminal) stages — the single source of truth for "open deal", shared by
// the pipeline header's open-pipeline total and the Overdue close-date facet.
export const OPEN_STAGES = STAGE_ORDER.filter(s => s !== 'won' && s !== 'lost');

// Stage hues live in index.css as `--color-ck-stage-*` tokens so the `.dark` block can
// lighten them for dark surfaces — the previous hand-tuned hex/rgba table was correct
// on light backgrounds only. The tint is derived with color-mix() rather than a second
// hand-maintained table, so both themes stay right from one source.
//
// Three keys, not two, and the names are the whole point (issue #119): `fill` paints the
// stage dot, the card's left border and the dashboard bars, `text` paints the stage label,
// and `bg` is the 12% wash that must stay derived from `fill` — a wash that darkened
// alongside its own label would give back the contrast the split just bought. The old
// single `color` key is deliberately GONE rather than repurposed, so every existing
// consumer has to say which role it meant instead of silently compiling as the wrong one.
const stage = (name: string): { text: string; fill: string; bg: string } => {
  const fill = `var(--color-ck-stage-${name})`;
  return { text: `var(--color-ck-stage-${name}-text)`, fill, bg: tint(fill, 12) };
};

export const STAGE_COLORS: Record<string, { text: string; fill: string; bg: string }> =
  Object.fromEntries(STAGE_ORDER.map(s => [s, stage(s)]));

// AI-estimated touch count (#16). Three "12-touches" bands: 0-4 dead zone (amber),
// 5-12 closing window (green), 13+ long-cycle (informational blue — the same hue
// AiBadge uses, meaning "an AI wrote this", deliberately not the brand accent).
//
// Lives HERE rather than beside TouchCountPill so every surface rendering a touch
// count shares ONE ramp. A second ramp on the dashboard (issue #76) would have made
// the same deal amber on the pipeline board and red on the dashboard, with amber
// flipping meaning between "danger, too few" and "diminishing returns, many".
// `text` (never a bare `color`) for the same reason STAGE_COLORS renamed its key: the
// wash stays derived from the FILL token while the number itself uses the darkened text
// token. Every consumer of this ramp paints a glyph, so there is no `fill` key to add.
export const TOUCH_COLORS = {
  low: { bg: tint(GOLD_FILL, 12), text: GOLD_TEXT },   // 0-4
  mid: { bg: tint(SAGE_FILL, 12), text: SAGE_TEXT },   // 5-12
  high: { bg: tint(AI_FILL, 12), text: AI_TEXT },      // 13+
} as const;

export function touchBand(count: number): keyof typeof TOUCH_COLORS {
  return count <= 4 ? 'low' : count <= 12 ? 'mid' : 'high';
}

/** Foreground colour for a touch count, NULL-safe (no count renders "—", dimmed). */
export function touchCountColor(count: number | null | undefined): string {
  return count == null ? INK_DIM : TOUCH_COLORS[touchBand(count)].text;
}

// Lead-score bands (#18): hot >=70, warm 40-69, cool <40. Lifted out of ScorePill with #77
// so the pill and the Contacts list's Score facet cut the range at the same two numbers —
// a second inline ternary is how a badge and the filter that claims to select it drift.
export type ScoreBand = 'hot' | 'warm' | 'cool';

export function scoreBand(score: number): ScoreBand {
  return score >= 70 ? 'hot' : score >= 40 ? 'warm' : 'cool';
}
