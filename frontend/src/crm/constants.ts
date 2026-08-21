import { tint, GOLD, SAGE, AI, INK_DIM } from '../shared/styles';

export const STAGE_ORDER = ['lead', 'qualified', 'proposal', 'negotiation', 'won', 'lost'];

// Open (non-terminal) stages — the single source of truth for "open deal", shared by
// the pipeline header's open-pipeline total and the Overdue close-date facet.
export const OPEN_STAGES = STAGE_ORDER.filter(s => s !== 'won' && s !== 'lost');

// Stage hues live in index.css as `--color-ck-stage-*` tokens so the `.dark` block can
// lighten them for dark surfaces — the previous hand-tuned hex/rgba table was correct
// on light backgrounds only. The tint is derived with color-mix() rather than a second
// hand-maintained table, so both themes stay right from one source.
const stage = (name: string): { color: string; bg: string } => {
  const color = `var(--color-ck-stage-${name})`;
  return { color, bg: tint(color, 12) };
};

export const STAGE_COLORS: Record<string, { color: string; bg: string }> =
  Object.fromEntries(STAGE_ORDER.map(s => [s, stage(s)]));

// AI-estimated touch count (#16). Three "12-touches" bands: 0-4 dead zone (amber),
// 5-12 closing window (green), 13+ long-cycle (informational blue — the same hue
// AiBadge uses, meaning "an AI wrote this", deliberately not the brand accent).
//
// Lives HERE rather than beside TouchCountPill so every surface rendering a touch
// count shares ONE ramp. A second ramp on the dashboard (issue #76) would have made
// the same deal amber on the pipeline board and red on the dashboard, with amber
// flipping meaning between "danger, too few" and "diminishing returns, many".
export const TOUCH_COLORS = {
  low: { bg: tint(GOLD, 12), color: GOLD },   // 0-4
  mid: { bg: tint(SAGE, 12), color: SAGE },   // 5-12
  high: { bg: tint(AI, 12), color: AI },      // 13+
} as const;

export function touchBand(count: number): keyof typeof TOUCH_COLORS {
  return count <= 4 ? 'low' : count <= 12 ? 'mid' : 'high';
}

/** Foreground colour for a touch count, NULL-safe (no count renders "—", dimmed). */
export function touchCountColor(count: number | null | undefined): string {
  return count == null ? INK_DIM : TOUCH_COLORS[touchBand(count)].color;
}
