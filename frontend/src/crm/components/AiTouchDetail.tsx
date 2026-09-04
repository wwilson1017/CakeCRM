// Drill-down for the AI touch count (issue #56): every event the scan evaluated, with a
// verdict and a reason. An AI-inferred number people can't interrogate is one they fight,
// so this is the trust surface for #16's pill.
//
// Zero-keys rule: a NULL count means no provider ever ran, so the whole section renders
// nothing — exactly the contract TouchCountPill already holds by returning null.
import { useEffect, useRef, useState } from 'react';

import { api } from '../../core/api/client';
import type { AiTouchEvidenceEvent, AiTouchEvidenceResponse } from '../../core/types';
import { INK, INK_DIM, INK_MUTE, INK_SOFT, GOLD_FILL, LINE, SAGE_FILL, SAGE_TEXT, mono, tint } from '../../shared/styles';
import { bannerCopy, coverageNote, stateLabel, summaryLine } from '../touchEvidence';
import { TouchCountPill } from './badges';

/** A verdict-coloured pill, but only for states the AI actually judged. */
function VerdictMarker({ event, open }: { event: AiTouchEvidenceEvent; open: boolean }) {
  const label = stateLabel(event.state, open);
  const base = { ...mono(10), whiteSpace: 'nowrap' as const, padding: '2px 8px', borderRadius: 4 };
  if (event.state === 'touch') {
    return <span style={{ ...base, background: tint(SAGE_FILL, 12), color: SAGE_TEXT }}>{label}</span>;
  }
  if (event.state === 'not_touch' || event.state === 'stage_move') {
    return <span style={{ ...base, background: tint(INK, 6), color: INK_SOFT }}>{label}</span>;
  }
  // An unjudged event must not wear a verdict-coloured pill — it would read as a ruling.
  return <span style={{ ...mono(10), color: INK_DIM, fontStyle: 'italic' }}>{label}</span>;
}

export function AiTouchDetail({ dealId, count }: { dealId: number; count?: number | null }) {
  const [expanded, setExpanded] = useState(false);
  const [fetchTick, setFetchTick] = useState(0);
  const [result, setResult] = useState<{ id: number; data: AiTouchEvidenceResponse } | null>(null);
  const [error, setError] = useState(false);
  // Latches the deal id we have already fetched, so expanding twice doesn't re-request.
  const fetched = useRef<number | null>(null);

  useEffect(() => {
    if (!expanded || fetched.current === dealId) return;
    fetched.current = dealId;
    // `cancelled` and `delivered` answer two different questions, and both are needed.
    // cancelled: this request was superseded (deal switched, collapsed, refresh) — its
    // response must be dropped, or a slow reply for the PREVIOUS deal could land last and
    // overwrite the current one, after which `result.id !== dealId` hides it while the
    // latch still says "fetched" — a spinner that never resolves.
    // delivered: the response actually made it into state, so the latch has to stay and
    // collapsing then re-expanding must NOT re-request.
    let cancelled = false;
    let delivered = false;
    setError(false);
    api<AiTouchEvidenceResponse>(`/api/crm/deals/${dealId}/touch-count/evidence`)
      .then(data => {
        if (cancelled) return;
        delivered = true;
        setResult({ id: dealId, data });
      })
      .catch(() => { if (!cancelled) setError(true); });
    return () => {
      cancelled = true;
      if (!delivered) fetched.current = null;
    };
  }, [expanded, dealId, fetchTick]);

  // A NULL count means no provider has ever run for this deal — nothing to explain.
  if (count == null) return null;

  // Read back only the response for the deal we are showing, so a dealId change resets
  // without a setState-in-effect (the repo's eslint react-hooks ruleset forbids that).
  const data = result && result.id === dealId ? result.data : null;
  // Prefer the endpoint's number over the frozen list-row prop: the pill came from a list
  // snapshot that nothing refreshes after a recompute.
  const shownCount = data?.ai_touch_count ?? count;
  const loading = expanded && !data && !error;
  const banner = data ? bannerCopy(data.verdict_state, data.open) : null;
  const coverage = data ? coverageNote(data.truncated) : null;
  const panelId = `ai-touch-detail-${dealId}`;

  return (
    <div style={{ border: `1px solid ${LINE}`, borderRadius: 6, marginBottom: 16 }}>
      <button
        type="button"
        onClick={() => setExpanded(v => !v)}
        aria-expanded={expanded}
        aria-controls={panelId}
        style={{
          display: 'flex', alignItems: 'center', gap: 10, width: '100%',
          padding: '10px 12px', background: 'none', border: 'none', cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        <span style={{ ...mono(10), color: INK_DIM }}>AI TOUCH COUNT</span>
        {/* The shared pill, not a local copy: mono() forces uppercase, so a hand-rolled
            chip renders "4 TOUCHES" next to the board's "4 touches" — the same number in
            two casings, one click apart. Reusing it also keeps one tooltip and one colour
            ramp app-wide (the #76 rule). */}
        <TouchCountPill count={shownCount} />
        <span style={{ ...mono(10), color: INK_DIM, marginLeft: 'auto' }}>
          {expanded ? '▲ evidence' : '▼ evidence'}
        </span>
      </button>

      {expanded && (
        <div id={panelId} style={{ borderTop: `1px solid ${LINE}`, padding: '10px 12px' }}>
          <p style={{ fontSize: 12, color: INK_MUTE, marginBottom: 10, lineHeight: 1.5 }}>
            The AI&apos;s estimate of real prospect contact on this deal — the dashboard&apos;s
            Weekly Touches counts something different (deals touched per window).
          </p>

          {loading && <p style={{ fontSize: 13, color: INK_DIM }}>Loading the evidence…</p>}
          {error && (
            <p role="alert" style={{ fontSize: 13, color: INK_MUTE }}>
              Could not load the evidence for this count.
            </p>
          )}

          {data && (
            <>
              {banner && (
                <p style={{
                  fontSize: 12, color: INK, background: tint(GOLD_FILL, 10), lineHeight: 1.5,
                  padding: '8px 10px', borderRadius: 4, marginBottom: 10,
                }}>
                  {banner}
                </p>
              )}
              {coverage && (
                <p style={{ fontSize: 12, color: INK_MUTE, marginBottom: 10 }}>{coverage}</p>
              )}

              {data.events.length === 0 ? (
                <p style={{ fontSize: 13, color: INK_MUTE }}>
                  No notes or activities recorded on this deal.
                </p>
              ) : (
                <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                  {data.events.map(event => (
                    <li
                      key={`${event.source}-${event.source_id ?? 'x'}`}
                      style={{ padding: '8px 0', borderTop: `1px solid ${LINE}` }}
                    >
                      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
                        {/* Customer-authored text: rendered as text, never as HTML. */}
                        <span style={{ fontSize: 13, color: INK, flex: 1, lineHeight: 1.5 }}>
                          {event.line}
                        </span>
                        <VerdictMarker event={event} open={data.open} />
                      </div>
                      {event.reason && (
                        <p style={{
                          fontSize: 12, color: INK_MUTE, fontStyle: 'italic',
                          margin: '4px 0 0', lineHeight: 1.5,
                        }}>
                          {event.reason}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              )}

              <div style={{
                display: 'flex', alignItems: 'center', gap: 10, marginTop: 10,
                paddingTop: 8, borderTop: `1px solid ${LINE}`,
              }}>
                <span style={{ fontSize: 12, color: INK_DIM }}>
                  {summaryLine(data.counted, data.evaluated, data.computed_at)}
                </span>
                <button
                  type="button"
                  onClick={() => { fetched.current = null; setFetchTick(t => t + 1); }}
                  style={{
                    ...mono(10), marginLeft: 'auto', background: 'none', border: 'none',
                    color: INK_SOFT, cursor: 'pointer', padding: 0,
                  }}
                >
                  REFRESH
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
