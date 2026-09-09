import { INK, INK_MUTE, INK_DIM, FONT_DISPLAY, mono } from '../../shared/styles';
import { cardStyle } from '../styles';

/** A small metric tile for the dashboard Snapshot row (issue #20). Presentational
 *  only — the parent passes already-formatted strings. `color` tints the value
 *  (e.g. SAGE_TEXT/GOLD_TEXT/CORAL_TEXT for a win-rate or overdue signal); defaults to INK. */
export function StatCard({ label, value, sub, color }: {
  label: string;
  value: string;
  sub?: string;
  color?: string;
}) {
  return (
    <div style={{ ...cardStyle, padding: '14px 16px', flex: '1 1 150px', minWidth: 132 }}>
      <div style={mono(10, INK_DIM)}>{label}</div>
      <div style={{
        fontFamily: FONT_DISPLAY, fontSize: 26, letterSpacing: '-0.01em',
        lineHeight: 1.1, margin: '6px 0 0', color: color ?? INK,
      }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: INK_MUTE, marginTop: 4 }}>{sub}</div>}
    </div>
  );
}
