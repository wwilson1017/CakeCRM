import { useState, useEffect, useRef } from 'react';
import type { CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../core/api/client';
import type { CrmWeeklyTouches } from '../../core/types';
import {
  INK, INK_MUTE, INK_DIM, LINE, CORAL_TEXT, ACCENT_TEXT,
  FONT_DISPLAY, mono, labelStyle, inputStyle,
} from '../../shared/styles';
import { cardStyle, sectionHeading, btnPrimary, btnSecondary } from '../styles';
import { RepLabel } from './RepLabel';
import { TouchDealRow } from './TouchDealRow';
import { ownerParamOf, touchDetailPath } from '../weeklyTouches';

/**
 * Weekly Touches KPI (issue #76) — how many deals got touched in a window, counting a
 * deal while it is open and up to and including its move to Won (#179).
 *
 * Grouped per DEAL OWNER since #146. #76 shipped it per deal because CakeCRM was
 * single-user and there were no owner columns; #60 landed `deals.owner_id`, so the rows
 * are now the reps the blueprint always had, each expanding to their own top deals. Every
 * owner of an open deal gets a row — including the ones who touched nothing, which is the
 * point of a weekly accountability pull — and so does anyone who won a deal inside the
 * window, so a rep whose only deal closed this week is credited rather than erased. The
 * unowned deals are a bucket named "Unassigned" rather than an exclusion, which is what
 * makes the headline the sum of the rows beneath it.
 *
 * `touches` and `open_deals` are two facts, never a ratio: a deal won this week was
 * touched but is no longer open, so touches can exceed open deals and both are rendered
 * side by side.
 *
 * The per-deal number is what #16 exists for: the 12-touches idea says deals close between
 * touch 5 and 12 and reps quit at 1-4, hence the colour ramp on each count (shared with
 * TouchCountPill via crm/constants.ts, so one number never renders in two colours).
 *
 * Two distinct signals, per the server: window MEMBERSHIP is keyless and event-grained
 * (edits, activities, live notes), while the per-deal NUMBER is #16's AI estimate — and a
 * lifetime-ish one, which the body copy says out loud so a "12" isn't read as twelve
 * touches this week.
 *
 * ZERO AI KEYS: renders nothing at all. `computed_deals === 0` means no touch count has
 * ever been computed, which is exactly the no-provider state (the touch-count worker needs
 * a light-tier model). Unchanged by the grouping, and still window-INDEPENDENT — a quiet
 * week must not look like a missing provider.
 *
 * A "mine only" scope was considered for this card (#146) and deliberately declined:
 * Weekly Touches is a comparison view, so hiding the other reps removes the feature. If it
 * is ever wanted, it is a CLIENT-side filter of `data.reps` by `useAuth().currentUser.id`
 * — the payload already carries every rep — not a backend parameter, whose absent/NULL
 * semantics would collide with the Unassigned bucket.
 *
 * `wrapperStyle` is the page's spacing/layering for this slot. The card owns it so that
 * hiding removes the padding too — a wrapper in the parent would leave a gap on the very
 * page this is meant to be invisible from. `refreshKey` lets the page refetch this card
 * along with the rest after a mutation.
 */

export function WeeklyTouchesCard(
  { wrapperStyle, refreshKey = 0, onOpenDeal }: {
    wrapperStyle?: CSSProperties;
    refreshKey?: number;
    // issue #56: opening the deal sheet is the per-event drill-down #76 deferred — the
    // sheet carries the evidence behind each of these numbers. The uncapped per-rep list
    // is the #146 detail page, linked from each rep row.
    onOpenDeal?: (dealId: number) => void;
  },
) {
  // Applied window: null = the rolling default (last 7 days); otherwise an
  // inclusive custom range. Only Apply commits the inputs, so typing a half-entered
  // date never fires a request the backend would 400.
  const [applied, setApplied] = useState<{ start: string; end: string } | null>(null);
  const [startInput, setStartInput] = useState('');
  const [endInput, setEndInput] = useState('');
  // The payload TOGETHER with the range that produced it. Keeping them in one piece of
  // state is what makes the Details links honest: `applied` moves the moment Apply or
  // reset is pressed, but a FAILED refetch deliberately leaves the previous numbers on
  // screen — so a link built from `applied` would point at the new (or failed) range while
  // the counts beside it still describe the old one, and reset-then-fail would send the
  // user to the rolling default from a card showing a custom week.
  const [result, setResult] = useState<
    { data: CrmWeeklyTouches; range: { start: string; end: string } | null } | null
  >(null);
  const [loading, setLoading] = useState(true);
  const [fetchFailed, setFetchFailed] = useState(false);
  // Which rep rows are open. Keyed by the URL spelling of the bucket so the null owner has
  // a key at all — `null` and `0` would collide in a Set<number>.
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
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
      // `applied` is captured from this render, so the stored range is exactly the one
      // this response answers — not whatever the control has moved on to since.
      .then(d => {
        if (id === reqId.current) { setResult({ data: d, range: applied }); setFetchFailed(false); }
      })
      // Log before hiding: a 500 from a broken query would otherwise be pixel-identical
      // to the intended zero-keys hide, so a real regression could ship unnoticed.
      //
      // On the FIRST load a failure hides the card (a broken nudge panel is worse than
      // an absent one). On a REFETCH it must not: the user pressed Apply, and blanking
      // the card would take the date inputs and the reset button with it — their own
      // action would look like it broke the feature, with no way back. So keep the last
      // good data and show an inline error beside Apply instead. Leaving `result` alone
      // does both: it is still null on a failed first load, and still the last good
      // payload — and the last good RANGE — on a failed refetch.
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
  const toggle = (key: string) => setExpanded(prev => {
    const next = new Set(prev);
    if (!next.delete(key)) next.add(key);
    return next;
  });

  const data = result?.data ?? null;
  // Read out alongside `data` so the link and the numbers are narrowed together —
  // `null` here legitimately means the rolling default, not "not loaded".
  const shownRange = result?.range ?? null;

  // Hidden affordance, never an error (product rule): no provider ⇒ nothing computed.
  // `loading` is checked first so the card doesn't flash in and out on mount.
  if (loading || !data || data.computed_deals === 0) return null;

  // The degenerate case #146 preserves: one bucket renders its deals flat, with no
  // expander and no indent — visually what the card was before the re-grouping. Derived
  // from the PAYLOAD, not from the user roster: a single-seat install whose assistant or
  // importer created unowned deals genuinely has two buckets, and both must show.
  const single = data.reps.length === 1;

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
        Deals edited, noted, or logged against in this window while open — through the
        move to Won, and never after. However many times, each deal counts once. Creating
        a deal doesn't count. The number beside each deal is its AI-estimated<em> lifetime</em>
        {' '}touch count, not this window's.
        {!single && ' Grouped by deal owner.'}
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
        {' deals touched · '}
        <span style={{ color: INK }}>{data.total_open_deals}</span>
        {' open'}
      </div>

      {data.reps.length === 0 ? (
        <p style={{ fontSize: 13, color: INK_DIM, margin: 0 }}>
          No deals touched in this window.
        </p>
      ) : (
        <div style={{ borderTop: `1px solid ${LINE}` }}>
          {data.reps.map(rep => {
            const key = ownerParamOf(rep.user_id);
            const open = single || expanded.has(key);
            const unassigned = rep.user_id === null;
            // The range these NUMBERS came from, not the one the control currently
            // holds — see `result`. And a range, not the payload's instants: the page
            // re-resolves the same window kind, so a deal touched since the card loaded
            // stays in the list rather than falling past a frozen upper bound.
            const detailPath = touchDetailPath(rep.user_id, shownRange);
            return (
              <div key={key}>
                {/* Rendered in the SERVER's order and never re-sorted here: the backend
                    sinks Unassigned last and ranks the rest by touches, and a second sort
                    would only be a place for the two to disagree. */}
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 12,
                  padding: single ? '0 0 8px' : '10px 0',
                  borderBottom: single ? undefined : `1px solid ${LINE}`,
                }}>
                  {/* Two SIBLING controls — a link nested inside a button is neither
                      clickable as a link nor announced as one. The single-rep case keeps
                      the label (an owner must always render, #128) but drops the toggle,
                      since there is nothing to collapse. */}
                  {single ? (
                    <div style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'baseline', gap: 8 }}>
                      <RepLabel name={rep.name} unassigned={unassigned} />
                      <RepCount touches={rep.touches} openDeals={rep.open_deals} />
                    </div>
                  ) : (
                    <button
                      type="button"
                      aria-expanded={open}
                      onClick={() => toggle(key)}
                      style={{
                        flex: 1, minWidth: 0, display: 'flex', alignItems: 'baseline', gap: 8,
                        background: 'none', border: 'none', padding: 0, cursor: 'pointer',
                        textAlign: 'left', font: 'inherit',
                      }}
                    >
                      <span aria-hidden="true" style={mono(10, INK_DIM)}>{open ? '▾' : '▸'}</span>
                      <RepLabel name={rep.name} unassigned={unassigned} />
                      <RepCount touches={rep.touches} openDeals={rep.open_deals} />
                    </button>
                  )}
                  {rep.touches > 0 && (
                    <Link
                      to={detailPath}
                      aria-label={`All touched deals for ${rep.name}`}
                      style={{ ...mono(10, ACCENT_TEXT), textDecoration: 'none', flexShrink: 0 }}
                    >Details →</Link>
                  )}
                </div>

                {open && (rep.deals.length === 0 ? (
                  <p style={{
                    fontSize: 13, color: INK_DIM, margin: 0,
                    padding: '10px 0', paddingLeft: single ? 0 : 18,
                  }}>
                    No deals touched in this window.
                  </p>
                ) : (
                  <>
                    {rep.deals.map(deal => (
                      <TouchDealRow
                        key={deal.id}
                        deal={deal}
                        onOpen={onOpenDeal}
                        indent={!single}
                      />
                    ))}
                    {/* The server caps each rep's rows; without this a rep's headline (12)
                        and their list (10) silently disagree. Derived from the two numbers,
                        so it needs no knowledge of the server's limit. */}
                    {rep.touches > rep.deals.length && (
                      <div style={{
                        ...mono(10, INK_DIM),
                        padding: '10px 0', paddingLeft: single ? 0 : 18,
                      }}>
                        Showing the top {rep.deals.length} of {rep.touches} touched deals ·{' '}
                        <Link to={detailPath} style={{ color: ACCENT_TEXT }}>See all</Link>
                      </div>
                    )}
                  </>
                ))}
              </div>
            );
          })}
        </div>
      )}
      </div>
    </div>
  );
}

function RepCount({ touches, openDeals }: { touches: number; openDeals: number }) {
  return (
    <span style={{ fontSize: 13, color: INK_MUTE, flexShrink: 0 }}>
      <span style={{ fontFamily: FONT_DISPLAY, fontSize: 17, color: INK }}>{touches}</span>
      {' touched · '}{openDeals} open
    </span>
  );
}
