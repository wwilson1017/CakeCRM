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
  return { color, bg: `color-mix(in srgb, ${color} 12%, transparent)` };
};

export const STAGE_COLORS: Record<string, { color: string; bg: string }> =
  Object.fromEntries(STAGE_ORDER.map(s => [s, stage(s)]));
