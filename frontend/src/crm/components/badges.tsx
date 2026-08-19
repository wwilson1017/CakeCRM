import { FONT_MONO, CORAL, GOLD, SAGE, INK, INK_SOFT, INK_DIM, AI, tint } from '../../shared/styles';

const badgeBase: React.CSSProperties = {
  fontSize: 12, padding: '4px 12px', borderRadius: 4,
  fontFamily: FONT_MONO, letterSpacing: '0.1em',
  textTransform: 'capitalize', fontWeight: 500,
};

const PRIORITY_COLORS: Record<string, { bg: string; color: string }> = {
  urgent: { bg: tint(CORAL, 15), color: CORAL },
  high: { bg: tint(CORAL, 12), color: CORAL },
  medium: { bg: tint(GOLD, 10), color: GOLD },
  low: { bg: tint(INK, 6), color: INK_SOFT },
};

export function PriorityBadge({ priority }: { priority: string }) {
  const c = PRIORITY_COLORS[priority] || PRIORITY_COLORS.medium;
  return <span style={{ ...badgeBase, background: c.bg, color: c.color }}>{priority}</span>;
}

const STATUS_COLORS: Record<string, { bg: string; color: string }> = {
  active: { bg: tint(SAGE, 12), color: SAGE },
  inactive: { bg: tint(INK, 5), color: INK_DIM },
  archived: { bg: tint(CORAL, 8), color: CORAL },
};

export function StatusBadge({ status }: { status: string }) {
  const c = STATUS_COLORS[status] || STATUS_COLORS.inactive;
  return <span style={{ ...badgeBase, background: c.bg, color: c.color }}>{status}</span>;
}

// Informational "AI" blue, shared by the touch-count high band and AiBadge (issue #16).
// Deliberately not the accent: these badges mean "an AI wrote this", not "brand". Now a
// themed token (`--color-ck-ai`) so it lightens on dark surfaces like every other hue.
export const AI_BLUE = AI;
export const AI_BLUE_SOFT = tint(AI, 12);

// AI-estimated touch count (issue #16). Three "12-touches" bands: 0-4 dead zone (amber),
// 5-12 closing window (green), 13+ long-cycle (blue). A NULL count renders nothing — the
// zero-keys degradation rule holds by construction (no count is written without a provider).
const TOUCH_COLORS = {
  low: { bg: tint(GOLD, 12), color: GOLD },   // 0-4
  mid: { bg: tint(SAGE, 12), color: SAGE },   // 5-12
  high: { bg: AI_BLUE_SOFT, color: AI_BLUE },          // 13+
} as const;

export function TouchCountPill({ count }: { count?: number | null }) {
  if (count == null) return null;
  const band = count <= 4 ? 'low' : count <= 12 ? 'mid' : 'high';
  const c = TOUCH_COLORS[band];
  const label = count === 1 ? '1 touch' : `${count} touches`;
  return (
    <span
      title="AI-estimated touches, from recent notes & activities. Most deals close between touch 5 and 12."
      style={{ ...badgeBase, textTransform: 'none', padding: '2px 8px', background: c.bg, color: c.color }}
    >
      {label}
    </span>
  );
}

// Lead score (issue #18): a pure-algorithmic 0-100 "how alive is this?" number on deals and
// contacts. Three bands — hot (>=70, green), warm (40-69, amber), cool (<40, muted). A NULL
// score renders nothing (never-scored rows show no pill). NOT an AI feature — always on.
const SCORE_COLORS = {
  cool: { bg: tint(INK, 6), color: INK_SOFT },  // <40
  warm: { bg: tint(GOLD, 12), color: GOLD },    // 40-69
  hot: { bg: tint(SAGE, 12), color: SAGE },     // >=70
} as const;

export function ScorePill({ score, compact }: { score?: number | null; compact?: boolean }) {
  if (score == null) return null;
  const band = score >= 70 ? 'hot' : score >= 40 ? 'warm' : 'cool';
  const c = SCORE_COLORS[band];
  return (
    <span
      title="Computed lead score (0-100) from stage, engagement, value, links & recency. Ask the assistant for the full breakdown."
      style={{ ...badgeBase, textTransform: 'none', padding: '2px 8px', background: c.bg, color: c.color }}
    >
      {compact ? score : `Score ${score}`}
    </span>
  );
}
