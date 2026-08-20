import { useState, useEffect, useRef } from 'react';
import type { CSSProperties } from 'react';
import { api } from '../../core/api/client';
import type { CrmWeeklyTouches } from '../../core/types';
import {
  INK, INK_MUTE, INK_DIM, LINE, GOLD, SAGE, CORAL,
  FONT_DISPLAY, mono, labelStyle, inputStyle, formatNumber,
} from '../../shared/styles';
import { cardStyle, sectionHeading, btnPrimary, btnSecondary } from '../styles';

/**
 * Weekly Touches KPI (issue #76) — how many open deals got touched in a window,
 * from #16's AI touch counts.
 *
 * The blueprint (cake_os WeeklyTouchesCard) breaks this down per rep; CakeCRM is
 * single-user, so the rows are per DEAL instead — which also surfaces the number
 * #16 exists for: the 12-touches idea says deals close between touch 5 and 12, and
 * reps quit at 1-4. Hence the colour ramp on each count.
 *
 * ZERO AI KEYS: renders nothing at all. `computed_deals === 0` means no touch count
 * has ever been computed, which is exactly the no-provider state (the touch-count
 * worker needs a light-tier model). A failed fetch hides it too — this is a nudge
 * panel, and a broken one is worse than an absent one.
 *
 * The drill-down list behind a number is issue #56; there is deliberately no link
 * here yet.
 *
 * `wrapperStyle` is the page's spacing/layering for this slot. The card owns it so
 * that hiding removes the padding too — a wrapper in the parent would leave a gap
 * on the very page this is meant to be invisible from.
 */

// Touch-count ramp — the 12-touches dead zone. Below 5 is where deals get dropped
// (CORAL), 5-12 is the closing window (SAGE), past 12 is diminishing returns (GOLD).
function touchColor(count: number | null): string {
  if (count === null) return INK_DIM;
  if (count < 5) return CORAL;
  if (count <= 12) return SAGE;
  return GOLD;
}

export function WeeklyTouchesCard({ wrapperStyle }: { wrapperStyle?: CSSProperties }) {
  // Applied window: null = the rolling default (last 7 days); otherwise an
  // inclusive custom range. Only Apply commits the inputs, so typing a half-entered
  // date never fires a request the backend would 400.
  const [applied, setApplied] = useState<{ start: string; end: string } | null>(null);
  const [startInput, setStartInput] = useState('');
  const [endInput, setEndInput] = useState('');
  const [data, setData] = useState<CrmWeeklyTouches | null>(null);
  const [loading, setLoading] = useState(true);
  // Monotonic id: a slow request for an old window must not overwrite a newer one.
  const reqId = useRef(0);

  useEffect(() => {
    const id = ++reqId.current;
    const qs = applied
      ? `?start=${encodeURIComponent(applied.start)}&end=${encodeURIComponent(applied.end)}`
      : '';
    // Stale-while-revalidate: the previous window's numbers stay on screen until the
    // new ones land, so applying a filter doesn't blank the card.
    api<CrmWeeklyTouches>(`/api/crm/dashboard/weekly-touches${qs}`)
      .then(d => { if (id === reqId.current) setData(d); })
      // Log before hiding: a 500 from a broken query would otherwise be pixel-identical
      // to the intended zero-keys hide, so a real regression could ship unnoticed.
      .catch(err => {
        console.error('Failed to load weekly touches:', err);
        if (id === reqId.current) setData(null);
      })
      .finally(() => { if (id === reqId.current) setLoading(false); });
  }, [applied]);

  const rangeInvalid = Boolean(startInput && endInput && endInput < startInput);
  const canApply = Boolean(startInput && endInput) && !rangeInvalid;

  const apply = () => { if (canApply) setApplied({ start: startInput, end: endInput }); };
  const reset = () => { setApplied(null); setStartInput(''); setEndInput(''); };

  // Hidden affordance, never an error (product rule): no provider ⇒ nothing computed.
  // `loading` is checked first so the card doesn't flash in and out on mount.
  if (loading || !data || data.computed_deals === 0) return null;

  return (
    <div style={wrapperStyle}>
    <div style={{ ...cardStyle, padding: '18px 20px' }}>
      <div style={{
        display: 'flex', flexWrap: 'wrap', alignItems: 'baseline',
        justifyContent: 'space-between', gap: 8,
      }}>
        <div style={{ ...sectionHeading(INK_DIM), marginBottom: 0 }}>Weekly touches</div>
        <span style={mono(10, INK_DIM)}>{data.window.label}</span>
      </div>
      <p style={{ fontSize: 12, color: INK_MUTE, margin: '8px 0 14px', maxWidth: 560 }}>
        Open deals with a logged interaction in this window. Multiple touches on one
        deal count once; counts are AI estimates from each deal's notes and activity.
      </p>

      {/* Date range filter */}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: 10, marginBottom: 16 }}>
        <label style={{ display: 'block' }}>
          <span style={labelStyle}>From</span>
          <input
            type="date"
            value={startInput}
            max={endInput || undefined}
            onChange={e => setStartInput(e.target.value)}
            style={{ ...inputStyle, width: 'auto', padding: '6px 10px', fontSize: 13 }}
          />
        </label>
        <label style={{ display: 'block' }}>
          <span style={labelStyle}>To</span>
          <input
            type="date"
            value={endInput}
            min={startInput || undefined}
            onChange={e => setEndInput(e.target.value)}
            style={{ ...inputStyle, width: 'auto', padding: '6px 10px', fontSize: 13 }}
          />
        </label>
        <button
          type="button"
          onClick={apply}
          disabled={!canApply}
          style={{
            ...btnPrimary, padding: '7px 16px', fontSize: 13,
            opacity: canApply ? 1 : 0.4, cursor: canApply ? 'pointer' : 'not-allowed',
          }}
        >Apply</button>
        {data.window.custom && (
          <button
            type="button"
            onClick={reset}
            style={{ ...btnSecondary, padding: '7px 16px', fontSize: 13 }}
          >Last 7 days</button>
        )}
        {rangeInvalid && (
          <span style={{ fontSize: 12, color: CORAL, alignSelf: 'center' }}>
            End date must be on or after start date.
          </span>
        )}
      </div>

      <div style={{ fontSize: 13, color: INK_MUTE, marginBottom: 12 }}>
        <span style={{ fontFamily: FONT_DISPLAY, fontSize: 20, color: INK }}>
          {data.total_touches}
        </span>
        {' '}of{' '}
        <span style={{ color: INK }}>{data.total_open_deals}</span>
        {' '}open deals touched
      </div>

      {data.deals.length === 0 ? (
        <p style={{ fontSize: 13, color: INK_DIM, margin: 0 }}>
          No open deals touched in this window.
        </p>
      ) : (
        <div style={{ borderTop: `1px solid ${LINE}` }}>
          {data.deals.map(deal => (
            <div key={deal.id} style={{
              padding: '10px 0', borderBottom: `1px solid ${LINE}`,
              display: 'flex', alignItems: 'center', gap: 12,
            }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{
                  fontSize: 14, color: INK,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>{deal.title}</div>
                <div style={{ ...mono(10, INK_MUTE), marginTop: 3 }}>
                  {deal.company_name || deal.contact_name || 'No contact'}
                </div>
              </div>
              <div style={{ textAlign: 'right', flexShrink: 0 }}>
                <div style={{
                  fontFamily: FONT_DISPLAY, fontSize: 17,
                  color: touchColor(deal.touch_count),
                }}>{deal.touch_count ?? '—'}</div>
                <div style={mono(10, INK_DIM)}>${formatNumber(deal.value)}</div>
              </div>
            </div>
          ))}
        </div>
      )}
      </div>
    </div>
  );
}
