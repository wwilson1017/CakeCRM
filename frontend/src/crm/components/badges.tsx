import {
  FONT_MONO, INK, INK_SOFT, INK_DIM, tint,
  CORAL_FILL, CORAL_TEXT, GOLD_FILL, GOLD_TEXT, SAGE_FILL, SAGE_TEXT, AI_FILL, AI_TEXT,
} from '../../shared/styles';
import { TOUCH_COLORS, touchBand, scoreBand } from '../constants';

const badgeBase: React.CSSProperties = {
  fontSize: 12, padding: '4px 12px', borderRadius: 4,
  fontFamily: FONT_MONO, letterSpacing: '0.1em',
  textTransform: 'capitalize', fontWeight: 500,
};

// Each row is a wash mixed from the FILL token carrying a glyph in the TEXT token —
// the pairing issue #119 exists to keep apart. Mixing the wash from the text token
// instead would darken the chip in step with its own label and undo the fix.
const PRIORITY_COLORS: Record<string, { bg: string; color: string }> = {
  urgent: { bg: tint(CORAL_FILL, 15), color: CORAL_TEXT },
  high: { bg: tint(CORAL_FILL, 12), color: CORAL_TEXT },
  medium: { bg: tint(GOLD_FILL, 10), color: GOLD_TEXT },
  low: { bg: tint(INK, 6), color: INK_SOFT },
};

export function PriorityBadge({ priority }: { priority: string }) {
  const c = PRIORITY_COLORS[priority] || PRIORITY_COLORS.medium;
  return <span style={{ ...badgeBase, background: c.bg, color: c.color }}>{priority}</span>;
}

const STATUS_COLORS: Record<string, { bg: string; color: string }> = {
  active: { bg: tint(SAGE_FILL, 12), color: SAGE_TEXT },
  inactive: { bg: tint(INK, 5), color: INK_DIM },
  archived: { bg: tint(CORAL_FILL, 8), color: CORAL_TEXT },
};

export function StatusBadge({ status }: { status: string }) {
  const c = STATUS_COLORS[status] || STATUS_COLORS.inactive;
  return <span style={{ ...badgeBase, background: c.bg, color: c.color }}>{status}</span>;
}

// Informational "AI" blue, shared by the touch-count high band and AiBadge (issue #16).
// Deliberately not the accent: these badges mean "an AI wrote this", not "brand". Now a
// themed token (`--color-ck-ai`) so it lightens on dark surfaces like every other hue.
export const AI_BLUE = AI_TEXT;
export const AI_BLUE_SOFT = tint(AI_FILL, 12);

// The touch-count ramp lives in crm/constants.ts so the dashboard's Weekly Touches
// card (#76) renders the same number in the same colour as this pill. A NULL count
// renders nothing here — the zero-keys degradation rule holds by construction (no
// count is written without a provider).
export function TouchCountPill({ count }: { count?: number | null }) {
  if (count == null) return null;
  const c = TOUCH_COLORS[touchBand(count)];
  const label = count === 1 ? '1 touch' : `${count} touches`;
  return (
    <span
      title="AI-estimated touches, from recent notes & activities. Most deals close between touch 5 and 12."
      style={{ ...badgeBase, textTransform: 'none', padding: '2px 8px', background: c.bg, color: c.text }}
    >
      {label}
    </span>
  );
}

// Lead score (issue #18): a pure-algorithmic 0-100 "how alive is this?" number on deals and
// contacts. Three bands — hot (>=70, green), warm (40-69, amber), cool (<40, muted). A NULL
// score renders nothing (never-scored rows show no pill). NOT an AI feature — always on.
const SCORE_COLORS = {
  cool: { bg: tint(INK, 6), color: INK_SOFT },        // <40
  warm: { bg: tint(GOLD_FILL, 12), color: GOLD_TEXT }, // 40-69
  hot: { bg: tint(SAGE_FILL, 12), color: SAGE_TEXT },  // >=70
} as const;

export function ScorePill({ score, compact }: { score?: number | null; compact?: boolean }) {
  if (score == null) return null;
  // Bands live in crm/constants.ts so the Contacts list's Score facet (#77) selects exactly
  // the rows this pill colours.
  const c = SCORE_COLORS[scoreBand(score)];
  return (
    <span
      title="Computed lead score (0-100) from stage, engagement, value, links & recency. Ask the assistant for the full breakdown."
      style={{ ...badgeBase, textTransform: 'none', padding: '2px 8px', background: c.bg, color: c.color }}
    >
      {compact ? score : `Score ${score}`}
    </span>
  );
}
