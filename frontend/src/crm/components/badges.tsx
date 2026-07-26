import { FONT_MONO, CORAL, GOLD, SAGE, INK_SOFT, INK_DIM } from '../../shared/styles';

const badgeBase: React.CSSProperties = {
  fontSize: 12, padding: '4px 12px', borderRadius: 4,
  fontFamily: FONT_MONO, letterSpacing: '0.1em',
  textTransform: 'capitalize', fontWeight: 500,
};

const PRIORITY_COLORS: Record<string, { bg: string; color: string }> = {
  urgent: { bg: 'rgba(194,65,65,0.15)', color: '#C24141' },
  high: { bg: 'rgba(194,65,65,0.12)', color: CORAL },
  medium: { bg: 'rgba(176,124,46,0.10)', color: GOLD },
  low: { bg: 'rgba(31,35,40,0.06)', color: INK_SOFT },
};

export function PriorityBadge({ priority }: { priority: string }) {
  const c = PRIORITY_COLORS[priority] || PRIORITY_COLORS.medium;
  return <span style={{ ...badgeBase, background: c.bg, color: c.color }}>{priority}</span>;
}

const STATUS_COLORS: Record<string, { bg: string; color: string }> = {
  active: { bg: 'rgba(46,125,79,0.12)', color: SAGE },
  inactive: { bg: 'rgba(31,35,40,0.05)', color: INK_DIM },
  archived: { bg: 'rgba(194,65,65,0.08)', color: CORAL },
};

export function StatusBadge({ status }: { status: string }) {
  const c = STATUS_COLORS[status] || STATUS_COLORS.inactive;
  return <span style={{ ...badgeBase, background: c.bg, color: c.color }}>{status}</span>;
}

// Informational "AI" blue, shared by the touch-count high band and AiBadge (issue #16).
// There is no brand-neutral blue token in shared/styles, and this must NOT be ACCENT
// (which is user-rebrandable), so it lives here as the single source both files import.
export const AI_BLUE = '#3A6CB0';
export const AI_BLUE_SOFT = 'rgba(58,108,176,0.12)';

// AI-estimated touch count (issue #16). Three "12-touches" bands: 0-4 dead zone (amber),
// 5-12 closing window (green), 13+ long-cycle (blue). A NULL count renders nothing — the
// zero-keys degradation rule holds by construction (no count is written without a provider).
const TOUCH_COLORS = {
  low: { bg: 'rgba(176,124,46,0.12)', color: GOLD },   // 0-4
  mid: { bg: 'rgba(46,125,79,0.12)', color: SAGE },    // 5-12
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
