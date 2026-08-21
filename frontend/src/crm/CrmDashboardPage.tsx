import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../core/api/client';
import type { CrmDashboard, CrmDeal, CrmAnalytics } from '../core/types';
import { ActivityTimeline } from './components/ActivityTimeline';
import { DealForm } from './components/DealForm';
import { DealDetailSheet } from './components/DealDetailSheet';
import { StatCard } from './components/StatCard';
import { WeeklyTouchesCard } from './components/WeeklyTouchesCard';
import { STAGE_COLORS, STAGE_ORDER } from './constants';
import { WarmHalo } from '../shared/WarmHalo';
import { useIsMobile } from '../shared/useIsMobile';
import { LoadError } from '../shared/LoadError';
import { toast } from '../shared/toast';
import {
  INK, INK_MUTE, INK_SOFT, INK_DIM, LINE,
  GOLD, ACCENT, SAGE, CORAL, BG_RAISED, FONT_DISPLAY,
  mono, formatNumber,
} from '../shared/styles';
import { sectionHeading, btnSecondary } from './styles';

// Aging-bucket fill color: severity ramp keyed on the numeric lower bound, so a
// backend label rename can't silently drop a bucket back to the neutral accent. The
// two oldest buckets stay OFF ACCENT so "stale" reads as a warning, not a highlight.
function bucketColor(minDays: number): string {
  if (minDays >= 91) return CORAL;
  if (minDays >= 31) return GOLD;
  return ACCENT;
}

// "YYYY-MM-DD" → "Mon D" (parsed as local midnight; display-only labels).
function fmtDay(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export function CrmDashboardPage() {
  const [data, setData] = useState<CrmDashboard | null>(null);
  const [analytics, setAnalytics] = useState<CrmAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedDeal, setSelectedDeal] = useState<CrmDeal | null>(null);
  const [editDeal, setEditDeal] = useState<CrmDeal | null>(null);
  // Bumped by reload() to refetch the weekly-touches card alongside the rest.
  const [touchesKey, setTouchesKey] = useState(0);
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
    // and bumps touchesKey so the weekly-touches card refetches with them —
    // otherwise logging an activity here updates every panel except that one.
    api<CrmDashboard>('/api/crm/dashboard').then(setData).catch(() => {});
    loadAnalytics();
    setTouchesKey(k => k + 1);
  }

  function openDeal(id: number) {
    // stale-deal rows carry only a summary; fetch the full deal for the sheet.
    // Analytics can be stale (deal deleted in another tab / by the assistant), so
    // on failure give feedback — a silent dead click reads as a broken UI, and
    // every other user action here toasts — and refresh the now-stale list.
    api<CrmDeal>(`/api/crm/deals/${id}`)
      .then(setSelectedDeal)
      .catch(() => { toast.error('Could not open that deal — it may have been deleted.'); loadAnalytics(); });
  }

  async function updateDealStage(deal: CrmDeal, stage: string) {
    try {
      await api(`/api/crm/deals/${deal.id}`, {
        method: 'PUT', body: JSON.stringify({ stage }),
      });
      setSelectedDeal(null);
      reload();
    } catch (err) {
      console.error('Failed to update deal stage:', err);
      toast.error('Failed to move deal.');
    }
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
    wl?.win_rate_pct == null ? undefined : wl.win_rate_pct >= 50 ? SAGE : wl.win_rate_pct > 0 ? GOLD : undefined;
  const agingBuckets = analytics?.aging.buckets ?? [];
  const openDealCount = agingBuckets.reduce((s, b) => s + b.count, 0);
  const maxBucket = Math.max(1, ...agingBuckets.map(b => b.count));
  const daily = analytics?.activity.daily ?? [];
  const maxDaily = Math.max(1, ...daily.map(d => d.count));
  const byType = analytics?.activity.by_type ?? [];
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
          Pipeline is <span style={{ color: GOLD, fontStyle: 'italic' }}>{totalPipelineValue}</span>
          <br /><span style={{ color: INK_MUTE, fontSize: isMobile ? 16 : 26 }}>across {totalDeals} open deals.</span>
        </h1>
      </div>

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
            color={data.overdue_tasks > 0 ? CORAL : undefined}
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
                <div style={{ width: `${wonPct}%`, background: SAGE }} />
                <div style={{ width: `${100 - wonPct}%`, background: CORAL }} />
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
        refreshKey={touchesKey}
        wrapperStyle={{ padding: `6px ${px} 22px`, position: 'relative', zIndex: 2 }}
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
                            background: STAGE_COLORS[stage.stage]?.color || INK_DIM,
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
                          background: STAGE_COLORS[stage.stage]?.color || ACCENT,
                        }} />
                      </div>
                    </>
                  ) : (
                    <>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <span style={{
                          width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                          background: STAGE_COLORS[stage.stage]?.color || INK_DIM,
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
                          background: STAGE_COLORS[stage.stage]?.color || ACCENT,
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
                  borderLeft: `3px solid ${STAGE_COLORS[d.stage]?.color || INK_DIM}`,
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
                      ...mono(10, d.days_since_touch >= analytics.stale_days * 2 ? CORAL : GOLD),
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
                <div key={deal.id} onClick={() => setSelectedDeal(deal)} style={{
                  padding: '14px 16px', marginBottom: 6,
                  display: 'flex', alignItems: 'center', gap: 14,
                  cursor: 'pointer',
                  background: STAGE_COLORS[deal.stage]?.bg || BG_RAISED,
                  border: `1px solid ${LINE}`,
                  borderLeft: `3px solid ${STAGE_COLORS[deal.stage]?.color || LINE}`,
                  borderRadius: 6,
                }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 16, letterSpacing: '-0.005em', color: INK }}>
                      {deal.title}
                      {!isMobile && <span style={{ color: INK_MUTE }}> · {deal.contact_name || 'No contact'}</span>}
                    </div>
                    <div style={{
                      ...mono(11, STAGE_COLORS[deal.stage]?.color || INK_DIM),
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

      {editDeal && <DealForm deal={editDeal} onClose={() => setEditDeal(null)} onSaved={() => { setEditDeal(null); setSelectedDeal(null); reload(); }} />}

      {selectedDeal && (
        <DealDetailSheet
          key={selectedDeal.id}
          deal={selectedDeal}
          isMobile={isMobile}
          onClose={() => { setSelectedDeal(null); reload(); }}
          onEdit={(d) => { setSelectedDeal(null); setEditDeal(d); }}
          onStageChange={updateDealStage}
        />
      )}
    </div>
  );
}
