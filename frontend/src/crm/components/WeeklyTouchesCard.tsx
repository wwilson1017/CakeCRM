import { useState, useEffect, useRef } from 'react';
import type { CSSProperties, KeyboardEvent } from 'react';
import { api } from '../../core/api/client';
import type { CrmWeeklyTouches } from '../../core/types';
import {
  INK, INK_MUTE, INK_DIM, LINE, CORAL_TEXT,
  FONT_DISPLAY, mono, labelStyle, inputStyle, formatNumber,
} from '../../shared/styles';
import { cardStyle, sectionHeading, btnPrimary, btnSecondary } from '../styles';
import { touchCountColor } from '../constants';

/**
 * Weekly Touches KPI (issue #76) — how many open deals got touched in a window.
 *
 * The blueprint (cake_os WeeklyTouchesCard) breaks this down per rep; CakeCRM is
 * single-user, so the rows are per DEAL instead — which also surfaces the number
 * #16 exists for: the 12-touches idea says deals close between touch 5 and 12, and
 * reps quit at 1-4. Hence the colour ramp on each count (shared with TouchCountPill
 * via crm/constants.ts, so one number never renders in two colours).
 *
 * Two distinct signals, per the server: window MEMBERSHIP is keyless and
 * event-grained (edits, activities, live notes), while the per-deal NUMBER is #16's
 * AI estimate — and a lifetime-ish one, which the body copy says out loud so a "12"
 * isn't read as twelve touches this week.
 *
 * ZERO AI KEYS: renders nothing at all. `computed_deals === 0` means no touch count
 * has ever been computed, which is exactly the no-provider state (the touch-count
 * worker needs a light-tier model).
 *
 * The drill-down list behind a number is issue #56; there is deliberately no link
 * here yet.
 *
 * `wrapperStyle` is the page's spacing/layering for this slot. The card owns it so
 * that hiding removes the padding too — a wrapper in the parent would leave a gap
 * on the very page this is meant to be invisible from. `refreshKey` lets the page
 * refetch this card along with the rest after a mutation.
 */

export function WeeklyTouchesCard(
  { wrapperStyle, refreshKey = 0, onOpenDeal }: {
    wrapperStyle?: CSSProperties;
    refreshKey?: number;
    // issue #56: opening the deal sheet is the drill-down #76 deliberately deferred —
    // the sheet carries the per-event evidence behind each of these numbers.
    onOpenDeal?: (dealId: number) => void;
  },
) {
  // Applied window: null = the rolling default (last 7 days); otherwise an
  // inclusive custom range. Only Apply commits the inputs, so typing a half-entered
  // date never fires a request the backend would 400.
  const [applied, setApplied] = useState<{ start: string; end: string } | null>(null);
  const [startInput, setStartInput] = useState('');
  const [endInput, setEndInput] = useState('');
  const [data, setData] = useState<CrmWeeklyTouches | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchFailed, setFetchFailed] = useState(false);
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
      .then(d => { if (id === reqId.current) { setData(d); setFetchFailed(false); } })
      // Log before hiding: a 500 from a broken query would otherwise be pixel-identical
      // to the intended zero-keys hide, so a real regression could ship unnoticed.
      //
      // On the FIRST load a failure hides the card (a broken nudge panel is worse than
      // an absent one). On a REFETCH it must not: the user pressed Apply, and blanking
      // the card would take the date inputs and the reset button with it — their own
      // action would look like it broke the feature, with no way back. So keep the last
      // good data and show an inline error beside Apply instead. Leaving `data` alone
      // does both: it is still null on a failed first load, and still the last good
      // payload on a failed refetch.
      .catch(err => {
        console.error('Failed to load weekly touches:', err);
        if (id === reqId.current) setFetchFailed(true);
      })
      .finally(() => { if (id === reqId.current) setLoading(false); });
  }, [applied, refreshKey]);

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
        Open deals edited, noted, or logged against in this window — however many times,
        each deal counts once. Creating a deal doesn't count. The number beside each deal
        is its AI-estimated<em> lifetime</em> touch count, not this window's.
      </p>

      {/* Date range filter */}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: 10, marginBottom: 16 }}>
        <label style={{ display: 'block' }}>
          <span style={labelStyle}>From (UTC)</span>
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
        {/* Gated on `applied`, not on the payload's `custom` flag: if a custom Apply
            fails, `applied` is set but `data` still describes the old window, and
            gating on the payload would hide the only control that gets back. */}
        {(applied !== null || data.window.custom) && (
          <button
            type="button"
            onClick={reset}
            style={{ ...btnSecondary, padding: '7px 16px', fontSize: 13 }}
          >Last 7 days</button>
        )}
        {rangeInvalid && (
          <span style={{ fontSize: 12, color: CORAL_TEXT, alignSelf: 'center' }}>
            End date must be on or after start date.
          </span>
        )}
        {!rangeInvalid && fetchFailed && (
          <span style={{ fontSize: 12, color: CORAL_TEXT, alignSelf: 'center' }}>
            Couldn't load that range — showing the last result.
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
            <div
              key={deal.id}
              {...(onOpenDeal ? {
                role: 'button',
                tabIndex: 0,
                onClick: () => onOpenDeal(deal.id),
                onKeyDown: (e: KeyboardEvent<HTMLDivElement>) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onOpenDeal(deal.id);
                  }
                },
              } : {})}
              style={{
                padding: '10px 0', borderBottom: `1px solid ${LINE}`,
                display: 'flex', alignItems: 'center', gap: 12,
                cursor: onOpenDeal ? 'pointer' : undefined,
              }}
            >
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
                <div
                  title="AI-estimated touches, from recent notes & activities. Most deals close between touch 5 and 12."
                  style={{
                    fontFamily: FONT_DISPLAY, fontSize: 17,
                    color: touchCountColor(deal.touch_count),
                  }}
                >{deal.touch_count ?? '—'}</div>
                <div style={mono(10, INK_DIM)}>${formatNumber(deal.value)}</div>
              </div>
            </div>
          ))}
          {/* The server caps the list; without this the headline (40) and the list (10)
              silently disagree. Derived from the two numbers, so it needs no knowledge
              of the server's limit. */}
          {data.total_touches > data.deals.length && (
            <div style={{ ...mono(10, INK_DIM), padding: '10px 0' }}>
              Showing the top {data.deals.length} of {data.total_touches} touched deals.
            </div>
          )}
        </div>
      )}
      </div>
    </div>
  );
}
