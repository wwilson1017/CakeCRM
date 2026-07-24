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

interface PipelineData {
  deals: CrmDeal[];
  stage_summary: { stage: string; count: number; total_value: number }[];
  total_pipeline_value: number;
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
  const [moving, setMoving] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const isMobile = useIsMobile();

  const columnRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const deepLinkDone = useRef(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await api<PipelineData>('/api/crm/deals');
      setData(d);
    } catch { /* data stays null → LoadError below */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { queueMicrotask(load); }, [load]);

  // Persist a stage change and optimistically fold it into `data` so column
  // counts/totals refresh without a spinner rebuild. Uses the PUT response (the
  // canonical deal incl. new updated_at + joined contact/company names) and
  // moves it to the front so grouped order matches a future updated_at-DESC
  // reload. Throws on failure — the caller decides how to react.
  const persistStageChange = useCallback(async (deal: CrmDeal, stage: string): Promise<CrmDeal> => {
    const updated = await api<CrmDeal>(`/api/crm/deals/${deal.id}`, {
      method: 'PUT', body: JSON.stringify({ stage }),
    });
    const merged = { ...deal, ...updated };
    setData(prev => prev ? {
      ...prev,
      deals: [merged, ...prev.deals.filter(d => d.id !== merged.id)],
    } : prev);
    return merged;
  }, []);

  // Drag handler: re-throws on failure so the Kanban hook rolls the card back to
  // its origin column. `moving` blocks a second drag until this PUT settles, so
  // the hook's single rollback snapshot can't be clobbered mid-flight.
  const handleKanbanMove = useCallback(async (event: MoveEvent<CrmDeal>) => {
    // Stage-only board: deals carry no rank column, so a same-stage reorder is
    // not persisted (it would reset on reload anyway).
    if (String(event.fromColumnId) === String(event.toColumnId)) return;
    setMoving(true);
    try {
      await persistStageChange(event.item, String(event.toColumnId));
    } catch (err) {
      console.error('Failed to move deal:', err);
      toast.error('Failed to move deal.');
      throw err; // drive the Kanban optimistic rollback
    } finally {
      setMoving(false);
    }
  }, [persistStageChange]);

  // Detail-sheet handler (fire-and-forget `void`): toast on failure, never
  // re-throw — a rejection here would be unhandled.
  const updateDealStage = useCallback(async (deal: CrmDeal, stage: string) => {
    try {
      await persistStageChange(deal, stage);
      setSelectedDeal(null);
    } catch (err) {
      console.error('Failed to update deal stage:', err);
      toast.error('Failed to move deal.');
    }
  }, [persistStageChange]);

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

  const openTotal = useMemo(
    () => deals.filter(d => OPEN_STAGES.includes(d.stage)).reduce((s, d) => s + (d.value || 0), 0),
    [deals],
  );
  const openCount = useMemo(
    () => deals.filter(d => OPEN_STAGES.includes(d.stage)).length,
    [deals],
  );

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
        // Drag off on touch (fiddly) and while a move is in flight (protects the
        // single rollback snapshot). Mobile stage changes go through the sheet.
        dragDisabled={isMobile || moving}
        className={`flex gap-4 overflow-x-auto pb-3 pt-1${isMobile ? ' snap-x snap-mandatory' : ''}`}
        columnClassName="flex flex-col gap-2 overflow-y-auto max-h-[70vh] min-h-[80px] pr-1"
        renderColumn={(col, children) => {
          const stage = String(col.id);
          const colDeals = grouped[stage] || [];
          const total = colDeals.reduce((s, d) => s + (d.value || 0), 0);
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
        renderCard={(deal) => (
          <DealBoardCard deal={deal} onOpen={() => setSelectedDeal(deal)} />
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

function DealBoardCard({ deal, onOpen }: { deal: CrmDeal; onOpen: () => void }) {
  const color = STAGE_COLORS[deal.stage]?.color || INK_DIM;
  const bg = STAGE_COLORS[deal.stage]?.bg || BG_CARD;
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
