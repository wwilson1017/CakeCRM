import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../core/api/client';
import type { CrmDeal } from '../core/types';
import { DealForm } from './components/DealForm';
import { DealDetailSheet } from './components/DealDetailSheet';
import { STAGE_COLORS, STAGE_ORDER } from './constants';
import { IconPlus } from '../shared/icons';
import { useIsMobile } from '../shared/useIsMobile';
import { LoadError } from '../shared/LoadError';
import { toast } from '../shared/toast';
import {
  INK, INK_MUTE, INK_DIM, LINE, BG_CARD,
  FONT_DISPLAY, mono, formatNumber,
} from '../shared/styles';
import { pageHeading, btnPrimary, stageCard } from './styles';
import { KanbanBoard, type MoveEvent } from '../shared/dnd';

// The /api/crm/deals payload also carries server-computed `stage_summary` and
// `total_pipeline_value`, but the board derives every total client-side from
// `deals` so they stay correct under optimistic moves — we intentionally read
// only `deals` here rather than trust aggregates the optimistic path can't update.
interface PipelineData {
  deals: CrmDeal[];
}

// Open stages drive the header subtitle; won/lost are terminal and excluded so
// the "open pipeline" total stays correct as deals are dragged in and out.
const OPEN_STAGES = STAGE_ORDER.filter(s => s !== 'won' && s !== 'lost');

export function PipelinePage() {
  const [data, setData] = useState<PipelineData | null>(null);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [editDeal, setEditDeal] = useState<CrmDeal | null>(null);
  const [selectedDeal, setSelectedDeal] = useState<CrmDeal | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const isMobile = useIsMobile();

  const columnRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const deepLinkDone = useRef(false);
  // Per-deal operation counter so out-of-order responses from rapid moves of the
  // SAME deal can't clobber each other — only the latest op reconciles/reverts.
  const dealOpSeq = useRef<Map<number, number>>(new Map());
  // Per-deal write chain: each deal's PUT is queued behind its prior in-flight
  // write so the SERVER applies moves in the user's action order (ending at the
  // latest intent), never racing two concurrent writes for the same deal.
  const dealWriteChain = useRef<Map<number, Promise<void>>>(new Map());

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await api<PipelineData>('/api/crm/deals');
      setData(d);
    } catch { /* data stays null → LoadError below */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { queueMicrotask(load); }, [load]);

  // `data` is the single source of truth for the board. A stage change is applied
  // to it optimistically — the deal is re-staged IN PLACE (its list position is
  // untouched, so a failed move needs no position bookkeeping) — so the card,
  // column counts, and totals all move together at drop time. Persistence runs in
  // the background: on success we reconcile with the canonical PUT response (new
  // updated_at, joined names); on failure we revert just this deal's stage and
  // toast. The ported Kanban hook mirrors `data` between gestures, so it re-syncs
  // to whichever branch wins — there is no separate rollback snapshot, and a
  // concurrent change made elsewhere (detail sheet, new deal) is never clobbered.
  //
  // Rapid moves of the SAME deal are made safe two ways: (1) the PUTs are chained
  // per deal so the server applies them in action order; (2) a per-deal op sequence
  // means only the latest op reconciles/reverts the client (no intermediate flicker
  // from an earlier op's response). NOTE: if two chained writes for one deal BOTH
  // fail, the client is left at the later op's intermediate stage — a rare double-
  // failure that self-heals on the next load; the deal always holds a valid stage.
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
        if (dealOpSeq.current.get(dealId) !== seq) return; // a newer move superseded this one
        setData(prev => prev ? {
          ...prev,
          deals: prev.deals.map(d => d.id === dealId ? { ...d, ...updated } : d),
        } : prev);
      } catch (err) {
        if (dealOpSeq.current.get(dealId) !== seq) return; // superseded — leave the newer state
        console.error('Failed to move deal:', err);
        toast.error('Failed to move deal.');
        setData(prev => prev ? {
          ...prev,
          deals: prev.deals.map(d => d.id === dealId ? { ...d, stage: fromStage } : d),
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

  const grouped = useMemo(
    () => STAGE_ORDER.reduce<Record<string, CrmDeal[]>>((acc, stage) => {
      acc[stage] = deals.filter(d => d.stage === stage);
      return acc;
    }, {}),
    [deals],
  );

  const kanbanColumns = useMemo(
    () => STAGE_ORDER.map(stage => ({ id: stage, data: { stage } })),
    [],
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

  const { openTotal, openCount } = useMemo(() => {
    const open = deals.filter(d => OPEN_STAGES.includes(d.stage));
    return { openTotal: open.reduce((s, d) => s + (d.value || 0), 0), openCount: open.length };
  }, [deals]);

  // Dashboard deep-link (/crm/pipeline?stage=X): once `data` has rendered the
  // columns (refs populated), scroll the requested column into view, then clear
  // only the `stage` param (preserving any others). Runs once.
  useEffect(() => {
    if (!data || deepLinkDone.current) return;
    const s = searchParams.get('stage');
    if (!s) return;
    deepLinkDone.current = true;
    if (STAGE_ORDER.includes(s)) {
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

      <KanbanBoard<CrmDeal, { stage: string }>
        columns={kanbanColumns}
        items={grouped}
        onMove={handleKanbanMove}
        // Drag off on touch (fiddly); mobile stage changes go through the sheet.
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
      </div>
    </div>
  );
}
