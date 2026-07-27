export const STAGE_ORDER = ['lead', 'qualified', 'proposal', 'negotiation', 'won', 'lost'];

// Open (non-terminal) stages — the single source of truth for "open deal", shared by
// the pipeline header's open-pipeline total and the Overdue close-date facet.
export const OPEN_STAGES = STAGE_ORDER.filter(s => s !== 'won' && s !== 'lost');

// Stage hues tuned for readable contrast on CakeCRM's warm-light surfaces
// (chatty's originals were tuned for a dark theme; the light-gray "qualified"
// in particular was near-invisible on white).
export const STAGE_COLORS: Record<string, { color: string; bg: string }> = {
  lead: { color: '#3B6EA5', bg: 'rgba(59,110,165,0.10)' },
  qualified: { color: '#64748B', bg: 'rgba(100,116,139,0.10)' },
  proposal: { color: '#B07C2E', bg: 'rgba(176,124,46,0.10)' },
  negotiation: { color: '#C26B3C', bg: 'rgba(194,107,60,0.10)' },
  won: { color: '#2E7D4F', bg: 'rgba(46,125,79,0.10)' },
  lost: { color: '#C24141', bg: 'rgba(194,65,65,0.10)' },
};
