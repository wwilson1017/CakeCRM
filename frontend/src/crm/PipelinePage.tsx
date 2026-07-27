import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../core/api/client';
import type { CrmDeal } from '../core/types';
import { DealForm } from './components/DealForm';
import { DealDetailSheet } from './components/DealDetailSheet';
import { TouchCountPill } from './components/badges';
import { STAGE_COLORS, STAGE_ORDER, OPEN_STAGES } from './constants';
import { IconPlus } from '../shared/icons';
import { useIsMobile } from '../shared/useIsMobile';
import { LoadError } from '../shared/LoadError';
import { toast } from '../shared/toast';
import {
  INK, INK_MUTE, INK_DIM, LINE, BG_CARD,
  FONT_DISPLAY, mono, formatNumber,
} from '../shared/styles';
import { pageHeading, btnPrimary, btnSecondary, btnSmall, stageCard } from './styles';
import { KanbanBoard, type MoveEvent } from '../shared/dnd';
import PipelineFilterBar from './components/PipelineFilterBar';
import {
  type PipelineFilterState, type AdvancedFilters,
  EMPTY_FILTER_STATE, dealMatchesAdvanced, hasAdvanced, loadFilterState, saveFilterState,
} from './pipelineFilters';

// The /api/crm/deals payload also carries server-computed `stage_summary` and
// `total_pipeline_value`, but the board derives every total client-side from
// `deals` so they stay correct under optimistic moves — we intentionally read
// only `deals` here rather than trust aggregates the optimistic path can't update.
interface PipelineData {
  deals: CrmDeal[];
}

export function PipelinePage() {
  const [data, setData] = useState<PipelineData | null>(null);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [editDeal, setEditDeal] = useState<CrmDeal | null>(null);
  const [selectedDeal, setSelectedDeal] = useState<CrmDeal | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const isMobile = useIsMobile();

  // Client-side facet filtering (issue #21). One envelope (search + advanced facets)
  // restored from / persisted to sessionStorage so a reload keeps the view, but it
  // never leaves the browser — filtering is a pure predicate over the already-loaded
  // board, no backend query params. Held as ONE object so restore/persist/clear-all
  // are single-path.
  const [filters, setFilters] = useState<PipelineFilterState>(() => {
    const restored = loadFilterState();
    // A dashboard deep-link (?stage=X) is authoritative over a restored stage facet: if
    // the saved facet would hide the target column, drop it at mount so the column exists
    // (matches the once-per-mount deep-link scroll below). Done here, not in an effect, to
    // avoid a cascading setState-in-effect.
    const s = searchParams.get('stage');
    if (s && STAGE_ORDER.includes(s) && restored.advanced.stages.length && !restored.advanced.stages.includes(s)) {
      return { ...restored, advanced: { ...restored.advanced, stages: [] } };
    }
    return restored;
  });
  const { search, advanced } = filters;
  useEffect(() => { saveFilterState(filters); }, [filters]);
  const setSearch = useCallback((s: string) => setFilters(f => ({ ...f, search: s })), []);
  const setAdvanced = useCallback((a: AdvancedFilters) => setFilters(f => ({ ...f, advanced: a })), []);

  const columnRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const deepLinkDone = useRef(false);
  // Per-deal operation counter so out-of-order responses from rapid moves of the
  // SAME deal can't clobber each other — only the latest op reconciles/reverts.
  const dealOpSeq = useRef<Map<number, number>>(new Map());
  // Per-deal write chain: each deal's PUT is queued behind its prior in-flight
  // write so the SERVER applies moves in the user's action order (ending at the
  // latest intent), never racing two concurrent writes for the same deal.
  const dealWriteChain = useRef<Map<number, Promise<void>>>(new Map());
  // Per-deal last server-CONFIRMED stage — the ground truth a failed move reverts
  // to. Seeded from each load and advanced on every successful PUT (even when the
  // display reconcile is superseded), so a rolled-back move restores the real
  // server stage rather than an optimistic intermediate that itself never persisted.
  const dealConfirmedStage = useRef<Map<number, string>>(new Map());

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await api<PipelineData>('/api/crm/deals');
      setData(d);
      dealConfirmedStage.current = new Map(d.deals.map(deal => [deal.id, deal.stage]));
    } catch { /* data stays null → LoadError below */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { queueMicrotask(load); }, [load]);

  // `data` is the single source of truth for the board. A stage change is applied
  // to it optimistically — the deal is re-staged IN PLACE (its list position is
  // untouched, so a failed move needs no position bookkeeping) — so the card,
  // column counts, and totals all move together at drop time. Persistence runs in
  // the background: on success we reconcile with the canonical PUT response (new
  // updated_at, joined names); on failure we revert this deal's stage to its last
  // server-confirmed value and toast. The ported Kanban hook mirrors `data` between
  // gestures, so it re-syncs to whichever branch wins — the only extra state is a
  // per-deal last-confirmed stage (server truth, not a board snapshot), and a
  // concurrent change made elsewhere (detail sheet, new deal) is never clobbered.
  //
  // Rapid moves of the SAME deal are made safe three ways: (1) the PUTs are chained
  // per deal so the server applies them in action order; (2) a per-deal op sequence
  // means only the latest op reconciles/reverts the client (no intermediate flicker
  // from an earlier op's response); (3) a failed move reverts to the deal's last
  // server-CONFIRMED stage (seeded on load, advanced on every successful PUT), not
  // its optimistic fromStage — so even if two chained writes for one deal BOTH fail,
  // the board rolls back to the true server stage instead of an intermediate stage
  // that never persisted.
  const moveDealStage = useCallback((deal: CrmDeal, toStage: string, fromStage: string) => {
    const dealId = deal.id;
    const seq = (dealOpSeq.current.get(dealId) ?? 0) + 1;
    dealOpSeq.current.set(dealId, seq);
    // Optimistic: restage the CURRENT record (not the captured drag snapshot, which
    // could be missing fields edited meanwhile), keeping its list position.
    setData(prev => prev ? {
      ...prev,
      deals: prev.deals.map(d => d.id === dealId ? { ...d, stage: toStage } : d),
    } : prev);
    const prior = dealWriteChain.current.get(dealId) ?? Promise.resolve();
    const run = prior.then(async () => {
      try {
        const updated = await api<CrmDeal>(`/api/crm/deals/${dealId}`, {
          method: 'PUT', body: JSON.stringify({ stage: toStage }),
        });
        // Record server truth for THIS write regardless of supersession — a later
        // failed move in the same chain reverts to a real confirmed stage, not an
        // optimistic intermediate. Use the response's stage, not toStage, so the
        // ground truth is whatever the server actually stored.
        dealConfirmedStage.current.set(dealId, updated.stage);
        if (dealOpSeq.current.get(dealId) !== seq) return; // a newer move superseded this one
        setData(prev => prev ? {
          ...prev,
          deals: prev.deals.map(d => d.id === dealId ? { ...d, ...updated } : d),
        } : prev);
      } catch (err) {
        if (dealOpSeq.current.get(dealId) !== seq) return; // superseded — leave the newer state
        console.error('Failed to move deal:', err);
        toast.error('Failed to move deal.');
        // Revert to the last server-confirmed stage, not this op's optimistic
        // fromStage: if an earlier chained write for this deal also failed, fromStage
        // is an intermediate the server never stored, so it would leave the board out
        // of sync until the next load. `?? fromStage` covers a deal with no confirmed
        // entry yet (a first move, where fromStage IS the confirmed stage).
        const confirmed = dealConfirmedStage.current.get(dealId) ?? fromStage;
        setData(prev => prev ? {
          ...prev,
          deals: prev.deals.map(d => d.id === dealId ? { ...d, stage: confirmed } : d),
        } : prev);
      }
    });
    dealWriteChain.current.set(dealId, run);
  }, []);

  // Drag handler. Resolves immediately so the Kanban hook ends its gesture and
  // re-syncs from `data` right away; persistence + rollback are data-driven (via
  // moveDealStage), never snapshot-driven, so this never needs to throw.
  const handleKanbanMove = useCallback((event: MoveEvent<CrmDeal>): Promise<void> => {
    const from = String(event.fromColumnId);
    const to = String(event.toColumnId);
    if (from === to) {
      // Same-column drop persists nothing (deals carry no rank column). Bump the
      // deals array reference so the hook re-syncs and drops the transient reorder
      // rather than leaving an unsaved arrangement that jumps back on next load.
      setData(prev => prev ? { ...prev, deals: prev.deals.slice() } : prev);
      return Promise.resolve();
    }
    moveDealStage(event.item, to, from);
    return Promise.resolve();
  }, [moveDealStage]);

  // Detail-sheet handler (Mark Won / Lost) — optimistic move + close the sheet.
  const updateDealStage = useCallback((deal: CrmDeal, stage: string) => {
    if (deal.stage !== stage) moveDealStage(deal, stage, deal.stage);
    setSelectedDeal(null);
  }, [moveDealStage]);

  const deals = useMemo(() => data?.deals ?? [], [data]);

  const isFiltering = search.trim() !== '' || hasAdvanced(advanced);

  // The board loads every deal, so advanced filtering is a pure client-side predicate
  // over `deals` — no refetch. This memo is spliced between `deals` and `grouped`; when
  // nothing is active it returns `deals` by reference so unfiltered renders don't churn.
  const filteredDeals = useMemo(() => {
    if (!isFiltering) return deals;
    const q = search.trim().toLowerCase();
    const now = new Date();
    return deals.filter(d => {
      if (q) {
        const hay = [d.title, d.contact_name, d.company_name].filter(Boolean).join(' ').toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return dealMatchesAdvanced(d, advanced, now);
    });
  }, [deals, search, advanced, isFiltering]);

  const grouped = useMemo(
    () => STAGE_ORDER.reduce<Record<string, CrmDeal[]>>((acc, stage) => {
      acc[stage] = filteredDeals.filter(d => d.stage === stage);
      return acc;
    }, {}),
    [filteredDeals],
  );

  // Stage facet doubles as a column filter: selecting stages hides the rest.
  const visibleStages = useMemo(
    () => (advanced.stages.length ? STAGE_ORDER.filter(s => advanced.stages.includes(s)) : STAGE_ORDER),
    [advanced.stages],
  );

  const kanbanColumns = useMemo(
    () => visibleStages.map(stage => ({ id: stage, data: { stage } })),
    [visibleStages],
  );

  // Per-stage value totals for the column headers — precomputed once per data
  // change so drag re-renders (which fire at pointer-move frequency) don't re-reduce
  // every column on every frame.
  const columnTotals = useMemo(() => {
    const totals: Record<string, number> = {};
    for (const stage of STAGE_ORDER) {
      totals[stage] = (grouped[stage] || []).reduce((s, d) => s + (d.value || 0), 0);
    }
    return totals;
  }, [grouped]);

  // Open-pipeline $/count reflect the FILTERED set so the header describes what's shown
  // (a "showing X of Y" annotation below signals when a filter is narrowing the board).
  const { openTotal, openCount } = useMemo(() => {
    const open = filteredDeals.filter(d => OPEN_STAGES.includes(d.stage));
    return { openTotal: open.reduce((s, d) => s + (d.value || 0), 0), openCount: open.length };
  }, [filteredDeals]);

  // Dashboard deep-link (/crm/pipeline?stage=X): once `data` has rendered the
  // columns (refs populated), scroll the requested column into view, then clear
  // only the `stage` param (preserving any others). Runs once.
  useEffect(() => {
    if (!data || deepLinkDone.current) return;
    const s = searchParams.get('stage');
    if (!s) return;
    deepLinkDone.current = true;
    if (STAGE_ORDER.includes(s)) {
      // The initializer already dropped any restored stage facet that would hide this
      // column, so the target is guaranteed present here.
      columnRefs.current.get(s)?.scrollIntoView({ behavior: 'smooth', inline: 'start', block: 'nearest' });
    }
    const next = new URLSearchParams(searchParams);
    next.delete('stage');
    setSearchParams(next, { replace: true });
  }, [data, searchParams, setSearchParams]);

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: '80px 0' }}>
        <div className="w-6 h-6 border-2 border-ck-accent border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!data) return <LoadError label="Couldn't load pipeline" onRetry={load} />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, padding: isMobile ? '20px 16px' : '32px 44px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: isMobile ? 16 : 24 }}>
        <div>
          <h1 style={pageHeading(isMobile)}>Pipeline</h1>
          <p style={{ fontSize: isMobile ? 14 : 20, color: INK_MUTE, marginTop: 6 }}>
            ${formatNumber(openTotal)} open · {openCount} open deal{openCount !== 1 ? 's' : ''}
            {isFiltering && (
              <span style={{ color: INK_DIM }}> · showing {filteredDeals.length} of {deals.length}</span>
            )}
          </p>
        </div>
        <button onClick={() => setShowCreate(true)} style={{
          ...btnPrimary,
          padding: '7px 14px', fontSize: 13,
          flexShrink: 0, marginTop: 8,
        }}>
          <IconPlus size={13} strokeWidth={2.25} /> {isMobile ? 'Add' : 'Add Deal'}
        </button>
      </div>

      <div style={{ marginBottom: isMobile ? 12 : 16 }}>
        <PipelineFilterBar
          search={search}
          advanced={advanced}
          onSearchChange={setSearch}
          onAdvancedChange={setAdvanced}
          isMobile={isMobile}
        />
      </div>

      {isFiltering && filteredDeals.length === 0 ? (
        <EmptyFilterState onClear={() => setFilters(EMPTY_FILTER_STATE)} />
      ) : (
      <KanbanBoard<CrmDeal, { stage: string }>
        columns={kanbanColumns}
        items={grouped}
        onMove={handleKanbanMove}
        // Drag stays ENABLED while filtering (only `isMobile` disables it). CakeCRM's
        // board is stage-only: `handleKanbanMove` ignores `MoveEvent.newIndex`, same-column
        // drops persist nothing, and `moveDealStage` restages by deal id against the full
        // `data.deals` — so a drop while a filter hides cards is index-safe by construction
        // (unlike the blueprint, whose board persisted intra-column order and disabled drag).
        // A drop that makes a deal stop matching an active facet just removes it from the
        // filtered view — correct filter semantics.
        dragDisabled={isMobile}
        // The ported KanbanBoard/KanbanColumn expose only className hooks (no style
        // prop), so board-scroller and column-body layout use Tailwind here; the
        // card and header visuals below use the CRM's inline design tokens.
        className={`flex gap-4 overflow-x-auto pb-3 pt-1${isMobile ? ' snap-x snap-mandatory' : ''}`}
        columnClassName="flex flex-col gap-2 overflow-y-auto max-h-[70vh] min-h-[80px] pr-1"
        renderColumn={(col, children) => {
          const stage = col.data.stage;
          const colDeals = grouped[stage] || [];
          const total = columnTotals[stage] || 0;
          return (
            <div
              key={col.id}
              data-stage={stage}
              ref={el => { if (el) columnRefs.current.set(stage, el); else columnRefs.current.delete(stage); }}
              style={{
                flexShrink: 0,
                width: isMobile ? '85vw' : 288,
                scrollSnapAlign: isMobile ? 'center' : undefined,
              }}
            >
              <StageHeader stage={stage} count={colDeals.length} total={total} />
              {children}
            </div>
          );
        }}
        renderCard={(deal, columnId) => (
          <DealBoardCard deal={deal} columnStage={String(columnId)} onOpen={() => setSelectedDeal(deal)} />
        )}
        renderEmptyColumn={() => (
          <div style={{
            fontSize: 12, color: INK_DIM, textAlign: 'center',
            padding: '16px 8px', border: `1px dashed ${LINE}`, borderRadius: 6,
          }}>No deals</div>
        )}
      />
      )}

      {showCreate && <DealForm onClose={() => setShowCreate(false)} onSaved={() => { setShowCreate(false); load(); }} />}
      {editDeal && <DealForm deal={editDeal} onClose={() => setEditDeal(null)} onSaved={() => { setEditDeal(null); setSelectedDeal(null); load(); }} />}

      {selectedDeal && (
        <DealDetailSheet
          key={selectedDeal.id}
          deal={selectedDeal}
          isMobile={isMobile}
          onClose={() => setSelectedDeal(null)}
          onEdit={(d) => { setSelectedDeal(null); setEditDeal(d); }}
          onStageChange={updateDealStage}
        />
      )}
    </div>
  );
}

// Shown in place of the board when active filters match no deals (avoids a row of
// empty stage columns reading as "no deals at all").
function EmptyFilterState({ onClear }: { onClear: () => void }) {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12,
      padding: '56px 24px', textAlign: 'center', border: `1px dashed ${LINE}`, borderRadius: 8,
    }}>
      <p style={{ fontSize: 15, color: INK_MUTE, margin: 0 }}>No deals match your filters.</p>
      <button onClick={onClear} style={{ ...btnSecondary, ...btnSmall }}>Clear filters</button>
    </div>
  );
}

function StageHeader({ stage, count, total }: { stage: string; count: number; total: number }) {
  const color = STAGE_COLORS[stage]?.color || INK_DIM;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, padding: '0 2px' }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', flexShrink: 0, background: color }} />
      <span style={{
        fontFamily: FONT_DISPLAY,
        fontSize: 15, letterSpacing: '-0.01em', textTransform: 'capitalize',
        color: INK,
      }}>{stage}</span>
      <span style={mono(10, INK_DIM)}>{count}</span>
      <span style={{ ...mono(10, INK_MUTE), marginLeft: 'auto' }}>${total.toLocaleString()}</span>
    </div>
  );
}

function DealBoardCard({ deal, columnStage, onOpen }: { deal: CrmDeal; columnStage: string; onOpen: () => void }) {
  // Colour from the column the card currently sits in (its bucket) rather than
  // deal.stage — during an optimistic drop the bucket updates before the deal's
  // own stage field does, so this keeps the accent correct instantly.
  const color = STAGE_COLORS[columnStage]?.color || INK_DIM;
  const bg = STAGE_COLORS[columnStage]?.bg || BG_CARD;
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(); } }}
      style={{ ...stageCard(bg, color), padding: '10px 12px', cursor: 'pointer' }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, marginBottom: 4 }}>
        <span style={{ fontSize: 13, color: INK, lineHeight: 1.3 }}>{deal.title}</span>
        <span style={{
          fontFamily: FONT_DISPLAY,
          fontSize: 14, color: INK, flexShrink: 0,
        }}>${deal.value.toLocaleString()}</span>
      </div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', fontSize: 11, color: INK_DIM }}>
        {deal.contact_name && <span>{deal.contact_name}</span>}
        {deal.probability > 0 && <span>{deal.probability}%</span>}
        {deal.expected_close_date && <span>{deal.expected_close_date}</span>}
        <TouchCountPill count={deal.ai_touch_count} />
      </div>
    </div>
  );
}
