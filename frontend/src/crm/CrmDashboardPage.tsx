import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../core/api/client';
import type { CrmDashboard, CrmDeal, CrmAnalytics } from '../core/types';
import { ActivityTimeline } from './components/ActivityTimeline';
import { DealDetailBody, type DealPatch } from './components/DealDetailBody';
import { CollectionDetail, denyEscapeBackdrop } from '../shared/collection';
import { DEAL_DETAIL_CONFIG } from './dealDetailConfig';
import { StatCard } from './components/StatCard';
import { TodayPanel } from './components/TodayPanel';
import { WeeklyTouchesCard } from './components/WeeklyTouchesCard';
import { STAGE_COLORS, STAGE_ORDER } from './constants';
import { stageWriteRequest } from './dealStageWrite';
import { WarmHalo } from '../shared/WarmHalo';
import { useIsMobile } from '../shared/useIsMobile';
import { LoadError } from '../shared/LoadError';
import { toast } from '../shared/toast';
import {
  INK, INK_MUTE, INK_SOFT, INK_DIM, LINE,
  GOLD_FILL, GOLD_TEXT, ACCENT, SAGE_FILL, SAGE_TEXT, CORAL_FILL, CORAL_TEXT, BG_RAISED, FONT_DISPLAY,
  mono, formatNumber,
} from '../shared/styles';
import { sectionHeading, btnSecondary } from './styles';
import { useUsers } from './useUsers';

const repCell: React.CSSProperties = { padding: '7px 8px', fontWeight: 400, whiteSpace: 'nowrap' };
const repNum: React.CSSProperties = { ...repCell, textAlign: 'right' };

// Aging-bucket fill color: severity ramp keyed on the numeric lower bound, so a
// backend label rename can't silently drop a bucket back to the neutral accent. The
// two oldest buckets stay OFF ACCENT so "stale" reads as a warning, not a highlight.
function bucketColor(minDays: number): string {
  if (minDays >= 91) return CORAL_FILL;
  if (minDays >= 31) return GOLD_FILL;
  return ACCENT;
}

// "YYYY-MM-DD" → "Mon D" (parsed as local midnight; display-only labels).
function fmtDay(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

// The `CollectionDetail` host config is shared with `PipelinePage` — see `dealDetailConfig.ts`.
// This page leans hardest on its `loadById`: it opens deals from three different queries and only
// the top-deals rows are ever in `items`, so every other row resolves through that fetch.

export function CrmDashboardPage() {
  const { users } = useUsers();
  const [data, setData] = useState<CrmDashboard | null>(null);
  const [analytics, setAnalytics] = useState<CrmAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedDealId, setSelectedDealId] = useState<number | null>(null);
  // Bumped by reload() to refetch the self-fetching cards alongside the rest.
  // Two consumers now: WeeklyTouchesCard (#76) and TodayPanel (#130).
  const [cardRefreshKey, setCardRefreshKey] = useState(0);
  // A monotonic count of SELECTION SESSIONS, bumped by every gesture that opens or closes the
  // panel. Read across the await in `updateDealStage`, so a settling write can tell "still the
  // panel I was closing" from "the user walked away and came back" — the deal id alone cannot,
  // since A -> B -> A reads as unchanged while the panel has remounted with a freshly editable
  // body underneath. See `PipelinePage` for why this is a funnel rather than an effect.
  const selectionEpoch = useRef(0);
  function selectDeal(id: number | null) {
    selectionEpoch.current++;
    setSelectedDealId(id);
  }

  const navigate = useNavigate();
  const isMobile = useIsMobile();
  // Monotonic id so a slow in-flight analytics request can't overwrite a newer
  // one (loadAnalytics fires from mount, reload, retry, and sheet-close).
  const analyticsReqId = useRef(0);

  // Best-effort: a failed analytics fetch degrades to the classic dashboard
  // (analytics sections just don't render) rather than blanking the page.
  function loadAnalytics() {
    const reqId = ++analyticsReqId.current;
    api<CrmAnalytics>('/api/crm/analytics')
      .then(a => { if (reqId === analyticsReqId.current) setAnalytics(a); })
      .catch(() => {});
  }

  function reload() {
    // refresh after a mutation; stale data beats a blank page. Refetches BOTH
    // dashboard and analytics so win/loss, activity, and staleness stay current,
    // and bumps cardRefreshKey so the cards that fetch their own data — Weekly
    // Touches and the Today panel — refetch with them; otherwise logging an
    // activity here would update every panel except those two.
    api<CrmDashboard>('/api/crm/dashboard').then(setData).catch(() => {});
    loadAnalytics();
    setCardRefreshKey(k => k + 1);
  }

  // The panel resolves the id itself: rows outside `top_deals` (stale deals, weekly touches)
  // carry only a summary, so `DEAL_DETAIL_CONFIG.loadById` fetches them. A deal that has since
  // been deleted now gets the layer's own "Record unavailable · Retry" panel — better feedback
  // than the toast this used to raise, and the stale list refreshes on close either way.
  function openDeal(id: number) {
    selectDeal(id);
  }

  async function updateDealStage(deal: CrmDeal, stage: string, lostReason?: string) {
    const sessionBefore = selectionEpoch.current;
    try {
      // `lostReason` is present only for a Mark Lost taken through the reason dialog,
      // which routes to the mark-lost verb instead of the plain stage PUT (issue #128).
      const { path, init } = stageWriteRequest(deal.id, stage, lostReason);
      await api(path, init);
      // Dismiss ONLY if this is still the same selection SESSION — see PipelinePage for why the
      // epoch rather than the deal id.
      if (selectionEpoch.current === sessionBefore) setSelectedDealId(null);
      reload();
    } catch (err) {
      console.error('Failed to update deal stage:', err);
      toast.error('Failed to move deal.');
    }
  }

  // The inline form's ONE save — stage and columns in the same PUT (see `DealPatch`). It rethrows
  // so the body can keep the user's draft on screen.
  //
  // The canonical row is patched from the PUT's own response BEFORE the broader reload starts,
  // and that is not belt-and-braces: `DealDetailBody`'s `view` deliberately lets the HOST row win
  // over its own detail fetch for any deal the host holds canonically, and a `top_deals` row is
  // exactly that. Leaving the stale row in place therefore shows pre-save values the moment edit
  // mode closes — and keeps showing them for good if the reload never lands. `reload()` is still
  // the reconciliation for everything else on the page (there is no board to patch a row into).
  async function saveDeal(deal: CrmDeal, patch: DealPatch): Promise<CrmDeal> {
    const updated = await api<CrmDeal>(`/api/crm/deals/${deal.id}`, {
      method: 'PUT', body: JSON.stringify(patch),
    });
    setData(prev => prev ? {
      // Merged, not replaced: the PUT response and the dashboard's `top_deals` query select
      // different columns. A deal that is not in the list is left alone.
      ...prev,
      top_deals: prev.top_deals.map(d => d.id === deal.id ? { ...d, ...updated } : d),
    } : prev);
    reload();
    // Handed back so the panel can fold the SERVER's row into its own read channel — the route
    // derives `probability` from the stage, so the patch alone is not what was stored.
    return updated;
  }

  // Doesn't set loading itself (the set-state-in-effect rule forbids sync
  // setState via the mount effect); initial state is true, retry sets it.
  function loadDashboard() {
    api<CrmDashboard>('/api/crm/dashboard')
      .then(setData)
      .catch(() => {}) // data stays null → LoadError below
      .finally(() => setLoading(false));
  }

  useEffect(() => { loadDashboard(); loadAnalytics(); }, []);

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: '80px 0' }}>
        <div className="w-8 h-8 border-2 border-ck-accent border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!data) return <LoadError label="Couldn't load CRM dashboard" onRetry={() => { setLoading(true); loadDashboard(); loadAnalytics(); }} />;

  const activePipeline = data.pipeline_by_stage
    .filter(s => s.stage !== 'won' && s.stage !== 'lost')
    .sort((a, b) => STAGE_ORDER.indexOf(a.stage) - STAGE_ORDER.indexOf(b.stage));
  const totalPipelineValue = `$${formatNumber(data.total_pipeline_value)}`;
  const totalDeals = activePipeline.reduce((s, p) => s + p.count, 0);

  const px = isMobile ? '20px' : '44px';

  // Analytics-derived view values (all null-safe: analytics may not have loaded).
  const wl = analytics?.win_loss;
  const closed = wl ? wl.deals_won + wl.deals_lost : 0;
  // Reuse the backend's win_rate_pct (won/closed) for the bar rather than recompute
  // the same ratio; when closed > 0 the backend guarantees it's non-null.
  const wonPct = closed > 0 ? Math.round(wl!.win_rate_pct ?? 0) : 0;
  const winRateColor =
    wl?.win_rate_pct == null ? undefined : wl.win_rate_pct >= 50 ? SAGE_TEXT : wl.win_rate_pct > 0 ? GOLD_TEXT : undefined;
  const agingBuckets = analytics?.aging.buckets ?? [];
  const openDealCount = agingBuckets.reduce((s, b) => s + b.count, 0);
  const maxBucket = Math.max(1, ...agingBuckets.map(b => b.count));
  const daily = analytics?.activity.daily ?? [];
  const maxDaily = Math.max(1, ...daily.map(d => d.count));
  const byType = analytics?.activity.by_type ?? [];
  const perRep = analytics?.per_rep ?? [];
  const maxType = Math.max(1, ...byType.map(t => t.count));

  return (
    <div style={{ position: 'relative', overflow: 'auto', height: '100%' }}>
      <WarmHalo opacity={0.3} />

      {/* Hero */}
      <div style={{ padding: isMobile ? '24px 20px 20px' : '36px 44px 28px', position: 'relative', zIndex: 2 }}>
        <div style={mono(10, INK_DIM)}>
          Week of {new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
        </div>
        <h1 style={{
          fontFamily: FONT_DISPLAY,
          fontSize: isMobile ? 30 : 48, fontWeight: 400, letterSpacing: '-0.02em',
          lineHeight: 1.1, margin: '10px 0 0', color: INK,
        }}>
          Pipeline is <span style={{ color: GOLD_TEXT, fontStyle: 'italic' }}>{totalPipelineValue}</span>
          <br /><span style={{ color: INK_MUTE, fontSize: isMobile ? 16 : 26 }}>across {totalDeals} open deals.</span>
        </h1>
      </div>

      {/* What needs you today (issue #130), above the stat row: the page's one
          "do this now" surface. Keyless, and self-hiding while it has nothing to say. */}
      <TodayPanel
        refreshKey={cardRefreshKey}
        wrapperStyle={{ padding: `0 ${px} 18px`, position: 'relative', zIndex: 2 }}
        onMutated={reload}
      />

      {/* Parity stat row (issue #76 — cake_os DashboardTab's four cards). Built from
          the dashboard payload ALONE, so it survives an analytics fetch failure; the
          Snapshot below needs /api/crm/analytics and vanishes without it, which is
          why overdue tasks appears in both places rather than only there. */}
      <div style={{ padding: `0 ${px} 18px`, position: 'relative', zIndex: 2 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
          <StatCard label="Contacts" value={data.total_contacts.toLocaleString()} />
          <StatCard label="Companies" value={data.total_companies.toLocaleString()} />
          <StatCard label="Pipeline value" value={totalPipelineValue} />
          <StatCard
            label="Overdue tasks"
            value={`${data.overdue_tasks}`}
            sub={`${data.pending_tasks} pending`}
            color={data.overdue_tasks > 0 ? CORAL_TEXT : undefined}
          />
        </div>
      </div>

      {/* Snapshot (analytics) */}
      {analytics && wl && (
        <div style={{ padding: `0 ${px} 4px`, position: 'relative', zIndex: 2 }}>
          {/* Win/loss + pipeline are all-time / current-state (the stats query is not
              date-windowed) — so NO "last N days" qualifier here; that belongs only on
              the genuinely windowed Activity section below. */}
          <div style={sectionHeading(INK_SOFT)}>Snapshot</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
            <StatCard
              label="Win rate"
              value={wl.win_rate_pct === null ? '—' : `${wl.win_rate_pct}%`}
              sub={`${wl.deals_won}W / ${wl.deals_lost}L`}
              color={winRateColor}
            />
            <StatCard
              label="Avg days to close"
              value={wl.avg_days_to_close === null ? '—' : `${wl.avg_days_to_close}d`}
              sub="won deals · approx"
            />
            <StatCard
              label="Avg won deal"
              value={wl.avg_won_deal_size === null ? '—' : `$${formatNumber(wl.avg_won_deal_size)}`}
            />
            <StatCard
              label="Open deals"
              value={`${wl.open_deals}`}
              sub={`worth $${formatNumber(wl.total_pipeline_value)}`}
            />
            {/* Overdue tasks deliberately NOT repeated here — it is the fourth tile of
                the always-on parity row above (#76), and rendering it twice on one
                screen read as an unfinished merge. */}
          </div>
          {closed > 0 ? (
            <div style={{ marginTop: 14, maxWidth: 420 }}>
              <div style={{ display: 'flex', height: 6, borderRadius: 3, overflow: 'hidden', background: LINE }}>
                <div style={{ width: `${wonPct}%`, background: SAGE_FILL }} />
                <div style={{ width: `${100 - wonPct}%`, background: CORAL_FILL }} />
              </div>
              <div style={{ ...mono(10, INK_DIM), marginTop: 5 }}>
                {wl.deals_won} won · {wl.deals_lost} lost
              </div>
            </div>
          ) : (
            <div style={{ marginTop: 12, fontSize: 13, color: INK_DIM }}>No closed deals yet.</div>
          )}
        </div>
      )}

      {/* Weekly touches (issue #76). Self-hiding with zero AI keys, so it owns its
          own padding — an empty wrapper here would leave a mystery gap on the page
          it is supposed to be invisible from. */}
      <WeeklyTouchesCard
        refreshKey={cardRefreshKey}
        wrapperStyle={{ padding: `6px ${px} 22px`, position: 'relative', zIndex: 2 }}
        // issue #56: the sheet carries the per-event evidence behind each touch count,
        // which is the drill-down #76 deferred to this issue.
        onOpenDeal={openDeal}
      />

      {/* Stage rows */}
      <div style={{ padding: `0 ${px} 28px`, position: 'relative', zIndex: 2 }}>
        <div style={{ borderTop: `1px solid ${LINE}` }}>
          {activePipeline.length === 0 ? (
            <p style={{ color: INK_DIM, fontSize: 13, padding: '16px 0' }}>No active deals yet.</p>
          ) : (
            activePipeline.map(stage => {
              const pct = data.total_pipeline_value > 0 ? (stage.total_value / data.total_pipeline_value) * 100 : 0;
              return (
                <div key={stage.stage} onClick={() => navigate(`/crm/pipeline?stage=${stage.stage}`)} style={{
                  padding: '16px 0', borderBottom: `1px solid ${LINE}`,
                  cursor: 'pointer',
                  ...(isMobile ? {
                    display: 'flex', flexDirection: 'column' as const, gap: 8,
                  } : {
                    display: 'grid', gridTemplateColumns: '150px 1fr 110px 80px',
                    gap: 20, alignItems: 'center',
                  }),
                }}>
                  {isMobile ? (
                    <>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <span style={{
                            width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                            background: STAGE_COLORS[stage.stage]?.fill || INK_DIM,
                          }} />
                          <span style={{
                            fontFamily: FONT_DISPLAY,
                            fontSize: 16, letterSpacing: '-0.01em',
                            textTransform: 'capitalize', color: INK,
                          }}>{stage.stage}</span>
                        </div>
                        <div style={{ display: 'flex', gap: 12, alignItems: 'baseline' }}>
                          <span style={{
                            fontFamily: FONT_DISPLAY,
                            fontSize: 16, color: INK,
                          }}>${formatNumber(stage.total_value)}</span>
                          <span style={{ ...mono(10, INK_MUTE) }}>{stage.count} deals</span>
                        </div>
                      </div>
                      <div style={{ height: 2, background: LINE, position: 'relative' }}>
                        <div style={{
                          position: 'absolute', inset: 0,
                          right: `${100 - Math.max(pct, 2)}%`,
                          background: STAGE_COLORS[stage.stage]?.fill || ACCENT,
                        }} />
                      </div>
                    </>
                  ) : (
                    <>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <span style={{
                          width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                          background: STAGE_COLORS[stage.stage]?.fill || INK_DIM,
                        }} />
                        <span style={{
                          fontFamily: FONT_DISPLAY,
                          fontSize: 20, letterSpacing: '-0.01em',
                          textTransform: 'capitalize', color: INK,
                        }}>{stage.stage}</span>
                      </div>
                      <div style={{ height: 2, background: LINE, position: 'relative' }}>
                        <div style={{
                          position: 'absolute', inset: 0,
                          right: `${100 - Math.max(pct, 2)}%`,
                          background: STAGE_COLORS[stage.stage]?.fill || ACCENT,
                        }} />
                      </div>
                      <div style={{
                        fontFamily: FONT_DISPLAY,
                        fontSize: 22, fontWeight: 400, textAlign: 'right',
                        letterSpacing: '-0.01em', color: INK,
                      }}>${formatNumber(stage.total_value)}</div>
                      <div style={{
                        ...mono(11, INK_MUTE),
                        textAlign: 'right',
                      }}>{stage.count} deals</div>
                    </>
                  )}
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Per-rep (issue #60). Hidden unless the install actually has a team: on one
          seat every row would just restate the totals above it. */}
      {analytics && perRep.length > 0 && users.length > 1 && (
        <div style={{ padding: `10px ${px} 0`, position: 'relative', zIndex: 2 }}>
          <div style={sectionHeading(INK_SOFT)}>By rep</div>
          <div style={{ borderTop: `1px solid ${LINE}`, overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ color: INK_SOFT, textAlign: 'left' }}>
                  <th style={repCell}>Rep</th>
                  <th style={repNum} title="Deals they own right now, all time.">Open</th>
                  <th style={repNum} title="Value of those open deals, all time.">Pipeline</th>
                  <th style={repNum} title="Deals they own that closed won, all time.">Won</th>
                  <th style={repNum} title="Deals they own that closed lost, all time.">Lost</th>
                  <th style={repNum} title="Distinct deals, contacts and companies this person touched in the window. Compare reps on this one.">
                    Records touched ({analytics.window_days}d)
                  </th>
                  <th style={repNum} title="Every entry they logged in the window. One bulk action can inflate it.">
                    Activity ({analytics.window_days}d)
                  </th>
                </tr>
              </thead>
              <tbody>
                {perRep.map(r => (
                  <tr key={r.user_id ?? 'unattributed'} style={{ borderTop: `1px solid ${LINE}` }}>
                    <td style={{ ...repCell, color: r.user_id === null ? INK_SOFT : undefined }}>
                      {r.name}
                    </td>
                    <td style={repNum}>{r.deals_open}</td>
                    <td style={repNum}>${Math.round(r.open_value).toLocaleString()}</td>
                    <td style={repNum}>{r.deals_won}</td>
                    <td style={repNum}>{r.deals_lost}</td>
                    <td style={repNum}>{r.records_touched}</td>
                    <td style={{ ...repNum, color: INK_SOFT }}>{r.activity_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p style={{ color: INK_SOFT, fontSize: 12, marginTop: 6 }}>
            Two different questions in one table, deliberately. The pipeline columns
            are <strong>current state, all time</strong> — deals this person owns. The
            two marked columns are <strong>windowed</strong> and count work they did,
            wherever they did it. “Unattributed” is the assistant, the Gmail scan and
            imported history — nobody is recorded as having done it.
          </p>
        </div>
      )}

      {/* Deal aging + activity volume (analytics) */}
      {analytics && (
        <div style={{
          padding: `10px ${px} 20px`, position: 'relative', zIndex: 2,
          display: isMobile ? 'flex' : 'grid',
          flexDirection: isMobile ? 'column' as const : undefined,
          gridTemplateColumns: isMobile ? undefined : '1fr 1fr',
          gap: isMobile ? 28 : 36,
        }}>
          {/* Deal aging */}
          <div>
            <div style={sectionHeading(INK_SOFT)}>Deal aging · open deals by age</div>
            <div style={{ borderTop: `1px solid ${LINE}` }}>
              {agingBuckets.map(b => {
                const pct = (b.count / maxBucket) * 100;
                return (
                  <div key={b.label} style={{
                    padding: '11px 0', borderBottom: `1px solid ${LINE}`,
                    display: 'grid', gridTemplateColumns: '64px 1fr 32px', gap: 14, alignItems: 'center',
                  }}>
                    <span style={mono(11, INK_MUTE)}>{b.label}d</span>
                    <div style={{ height: 2, background: LINE, position: 'relative' }}>
                      <div style={{
                        position: 'absolute', inset: 0,
                        right: `${100 - Math.max(pct, b.count ? 2 : 0)}%`,
                        background: bucketColor(b.min_days),
                      }} />
                    </div>
                    <span style={{ ...mono(11, INK), textAlign: 'right' }}>{b.count}</span>
                  </div>
                );
              })}
            </div>
            {openDealCount === 0 && (
              <p style={{ color: INK_DIM, fontSize: 13, padding: '10px 0' }}>No open deals yet.</p>
            )}

            <div style={{ ...sectionHeading(INK_SOFT), marginTop: 22 }}>
              Needs a touch · {analytics.aging.stale_count} idle {analytics.stale_days}+ days
            </div>
            {analytics.aging.stale_deals.length === 0 ? (
              <p style={{ color: INK_DIM, fontSize: 13, padding: '6px 0' }}>
                Nothing stale — every open deal has a recent touch.
              </p>
            ) : (
              analytics.aging.stale_deals.map(d => (
                <div key={d.id} onClick={() => openDeal(d.id)} style={{
                  padding: '12px 14px', marginBottom: 6,
                  display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer',
                  background: STAGE_COLORS[d.stage]?.bg || BG_RAISED,
                  border: `1px solid ${LINE}`,
                  borderLeft: `3px solid ${STAGE_COLORS[d.stage]?.fill || INK_DIM}`,
                  borderRadius: 6,
                }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{
                      fontSize: 15, color: INK,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>{d.title}</div>
                    <div style={{ ...mono(10, INK_MUTE), marginTop: 3 }}>
                      {d.company_name || d.contact_name || 'No contact'}
                    </div>
                  </div>
                  <div style={{ textAlign: 'right', flexShrink: 0 }}>
                    <div style={{ fontFamily: FONT_DISPLAY, fontSize: 16, color: INK }}>
                      ${formatNumber(d.value)}
                    </div>
                    <div style={{
                      ...mono(10, d.days_since_touch >= analytics.stale_days * 2 ? CORAL_TEXT : GOLD_TEXT),
                      marginTop: 2,
                    }}>{d.days_since_touch}d idle</div>
                  </div>
                </div>
              ))
            )}
          </div>

          {/* Activity volume */}
          <div>
            <div style={sectionHeading(INK_SOFT)}>
              Activity · {analytics.activity.total} in last {analytics.window_days} days
            </div>
            <div style={{
              display: 'flex', alignItems: 'flex-end', gap: 2,
              height: 64, borderBottom: `1px solid ${LINE}`,
            }}>
              {daily.map(pt => (
                <div key={pt.day} title={`${pt.day}: ${pt.count}`} style={{
                  flex: 1,
                  height: pt.count ? `${Math.max((pt.count / maxDaily) * 100, 4)}%` : 2,
                  background: pt.count ? ACCENT : LINE,
                  borderRadius: 1,
                }} />
              ))}
            </div>
            {daily.length > 0 && (
              <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 5 }}>
                <span style={mono(9, INK_DIM)}>{fmtDay(daily[0].day)}</span>
                <span style={mono(9, INK_DIM)}>{fmtDay(daily[daily.length - 1].day)}</span>
              </div>
            )}

            <div style={{ marginTop: 18 }}>
              {byType.length === 0 ? (
                <p style={{ color: INK_DIM, fontSize: 13, padding: '6px 0' }}>
                  No activity in the last {analytics.window_days} days.
                </p>
              ) : (
                byType.map(t => {
                  const pct = (t.count / maxType) * 100;
                  return (
                    <div key={t.activity} style={{
                      padding: '9px 0', borderBottom: `1px solid ${LINE}`,
                      display: 'grid', gridTemplateColumns: '90px 1fr 32px', gap: 12, alignItems: 'center',
                    }}>
                      <span style={{
                        ...mono(11, INK_MUTE), textTransform: 'capitalize',
                        minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      }}>{t.activity}</span>
                      <div style={{ height: 2, background: LINE, position: 'relative' }}>
                        <div style={{
                          position: 'absolute', inset: 0,
                          right: `${100 - Math.max(pct, 2)}%`, background: ACCENT,
                        }} />
                      </div>
                      <span style={{ ...mono(11, INK), textAlign: 'right' }}>{t.count}</span>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>
      )}

      {/* Top deals + activity */}
      <div style={{
        padding: `10px ${px} 40px`, position: 'relative', zIndex: 2,
        display: isMobile ? 'flex' : 'grid',
        flexDirection: isMobile ? 'column' as const : undefined,
        gridTemplateColumns: isMobile ? undefined : '1.4fr 1fr',
        gap: isMobile ? 28 : 36,
      }}>
        <div>
          <div style={sectionHeading(INK_SOFT)}>Top deals</div>
          <div style={{ borderTop: `1px solid ${LINE}` }}>
            {data.top_deals.length === 0 ? (
              <p style={{ color: INK_DIM, fontSize: 15, padding: '16px 0' }}>No deals yet.</p>
            ) : (
              data.top_deals.map(deal => (
                <div key={deal.id} onClick={() => selectDeal(deal.id)} style={{
                  padding: '14px 16px', marginBottom: 6,
                  display: 'flex', alignItems: 'center', gap: 14,
                  cursor: 'pointer',
                  background: STAGE_COLORS[deal.stage]?.bg || BG_RAISED,
                  border: `1px solid ${LINE}`,
                  borderLeft: `3px solid ${STAGE_COLORS[deal.stage]?.fill || LINE}`,
                  borderRadius: 6,
                }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 16, letterSpacing: '-0.005em', color: INK }}>
                      {deal.title}
                      {!isMobile && <span style={{ color: INK_MUTE }}> · {deal.contact_name || 'No contact'}</span>}
                    </div>
                    <div style={{
                      ...mono(11, STAGE_COLORS[deal.stage]?.text || INK_DIM),
                      marginTop: 3, textTransform: 'uppercase',
                    }}>{deal.stage}{isMobile && deal.contact_name ? ` · ${deal.contact_name}` : ''}</div>
                  </div>
                  <div style={{
                    fontFamily: FONT_DISPLAY,
                    fontSize: isMobile ? 17 : 20, letterSpacing: '-0.01em', color: INK,
                    flexShrink: 0,
                  }}>${formatNumber(deal.value)}</div>
                </div>
              ))
            )}
          </div>
        </div>

        <div>
          <div style={sectionHeading(INK_SOFT)}>Recent activity</div>
          <div style={{ borderTop: `1px solid ${LINE}` }}>
            <ActivityTimeline activities={data.recent_activity} onUpdate={reload} />
          </div>
        </div>
      </div>

      {/* Quick actions */}
      <div style={{ padding: `0 ${px} 40px`, display: 'flex', gap: 8, position: 'relative', zIndex: 2, flexWrap: 'wrap' }}>
        {[
          { label: '+ Add Contact', path: '/crm/contacts' },
          { label: '+ Add Deal', path: '/crm/pipeline' },
          { label: '+ Add Task', path: '/crm/tasks' },
        ].map(a => (
          <button
            key={a.label}
            onClick={() => navigate(a.path)}
            style={{
              ...btnSecondary,
              padding: '9px 16px', fontSize: 14,
            }}
          >{a.label}</button>
        ))}
      </div>

      <CollectionDetail<CrmDeal>
        config={DEAL_DETAIL_CONFIG}
        items={data.top_deals}
        selectedId={selectedDealId}
        onSelect={id => {
          if (id === null) { selectDeal(null); reload(); }
          else selectDeal(Number(id));
        }}
        // Nothing to navigate, deliberately. This page opens deals from three unrelated queries
        // (top deals, stale deals, weekly touches), so walking any one of them would page the
        // user through records they did not open from — and the layer's own rule is that
        // disabling beats guessing. `[]` is that answer; omitting the prop would mean something
        // different (derive an order), which there is no collection state here to derive from.
        navOrder={[]}
        detail={{
          render: (deal, ctx) => (
            <DealDetailBody
              deal={deal}
              onBoard={data.top_deals.some(d => d.id === deal.id)}
              // Always writable here, unlike the pipeline: there is no board for a deal to be
              // off, every list on this page is already filtered to live deals, and the "Needs a
              // touch" panel is a real place to close one from. An ARCHIVED deal is still gated,
              // but by the body itself — it reads `archived_at` from its own detail fetch, which
              // is the only thing that knows, since these rows carry no board state.
              stageWritable
              ctx={ctx}
              onMarkWon={d => updateDealStage(d, 'won')}
              onMarkLost={(d, lostReason) => updateDealStage(d, 'lost', lostReason)}
              onSaveDeal={saveDeal}
              // The archived banner and its Restore render on ANY host (issue #83). Without this
              // the restore would succeed server-side while the panel stayed open over stale
              // dashboard numbers.
              onRestored={restored => {
                setSelectedDealId(prev => (prev === restored.id ? null : prev));
                reload();
              }}
            />
          ),
          onRequestClose: denyEscapeBackdrop,
        }}
      />
    </div>
  );
}
