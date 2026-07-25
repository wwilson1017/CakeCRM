import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../../core/api/client';
import type { CrmDeal, CrmActivity } from '../../core/types';
import { STAGE_COLORS } from '../constants';
import { mono, INK, INK_MUTE, INK_DIM, LINE, LINE_STRONG, ACCENT_INK, GOLD, SAGE, FONT_DISPLAY } from '../../shared/styles';
import { modalOverlay, modalContent, mobileDragHandle, btnDanger } from '../styles';
import { ActivityTimeline } from './ActivityTimeline';
import { NotesThread } from './NotesThread';
import { usePublishActiveRecord } from '../RecordContext';

interface DealDetailSheetProps {
  deal: CrmDeal;
  isMobile: boolean;
  onClose: () => void;
  onEdit: (deal: CrmDeal) => void;
  onStageChange: (deal: CrmDeal, stage: string) => void;
}

export function DealDetailSheet({ deal, isMobile, onClose, onEdit, onStageChange }: DealDetailSheetProps) {
  // Publish this deal as the open record while the sheet is mounted (issue #14).
  // Deals have no route, so this IS the deal open/close signal for both Pipeline
  // and Dashboard — no edits to either page.
  usePublishActiveRecord('deal', deal.id, deal.title);

  // The pipeline passes a plain list-row deal (no activity). Fetch the detail so
  // the sheet can show the activity timeline alongside the chatter thread.
  const [activity, setActivity] = useState<CrmActivity[]>(deal.activity || []);
  const reqRef = useRef(0);
  const loadDetail = useCallback(async () => {
    const reqId = ++reqRef.current;
    try {
      const detail = await api<CrmDeal>(`/api/crm/deals/${deal.id}`);
      if (reqId !== reqRef.current) return;
      setActivity(detail.activity || []);
    } catch {
      // Non-fatal: the sheet still shows deal fields + chatter; leave activity as-is.
    }
  }, [deal.id]);

  useEffect(() => { queueMicrotask(loadDetail); }, [loadDetail]);

  return (
    <div
      onClick={onClose}
      // DESKTOP: lower ONLY this overlay below the assistant launcher button (z-40) so
      // the drawer can be opened WITH deal context (the only time deal context exists);
      // every other modalOverlay stays at 50 and correctly occludes the button.
      // MOBILE: the sheet is a full-width bottom sheet, so a poked-through launcher
      // would overlap the sheet's bottom-left controls and steal taps — keep it at 50
      // (button occluded). Deal-context-via-drawer is therefore desktop-only for now (#14).
      style={{ ...modalOverlay(isMobile), zIndex: isMobile ? 50 : 39 }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{ ...modalContent(isMobile), maxHeight: '85vh', overflowY: 'auto' }}
      >
        {isMobile && (
          <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 16 }}>
            <div style={mobileDragHandle} />
          </div>
        )}

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
          <h3 style={{
            fontFamily: FONT_DISPLAY,
            fontSize: 20, fontWeight: 400, letterSpacing: '-0.01em',
            color: INK, margin: 0, flex: 1,
          }}>{deal.title}</h3>
          <span style={{
            fontFamily: FONT_DISPLAY,
            fontSize: 20, color: GOLD, flexShrink: 0, marginLeft: 12,
          }}>${deal.value.toLocaleString()}</span>
        </div>

        <div style={{
          fontSize: 12, color: INK_MUTE, marginBottom: 16,
          textTransform: 'capitalize',
        }}>
          Stage: <span style={{ color: STAGE_COLORS[deal.stage]?.color || INK }}>{deal.stage}</span>
        </div>

        {deal.notes && (
          <p style={{ fontSize: 14, color: INK_MUTE, marginBottom: 16, lineHeight: 1.5 }}>
            {deal.notes}
          </p>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 20 }}>
          {deal.contact_name && (
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ ...mono(10), color: INK_DIM }}>Contact</span>
              <span style={{ fontSize: 13, color: INK }}>{deal.contact_name}</span>
            </div>
          )}
          {deal.probability > 0 && (
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ ...mono(10), color: INK_DIM }}>Probability</span>
              <span style={{ fontSize: 13, color: INK }}>{deal.probability}%</span>
            </div>
          )}
          {deal.expected_close_date && (
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ ...mono(10), color: INK_DIM }}>Expected Close</span>
              <span style={{ fontSize: 13, color: INK }}>{deal.expected_close_date}</span>
            </div>
          )}
        </div>

        {/* Activity timeline */}
        <div style={{ borderTop: `1px solid ${LINE}`, paddingTop: 16, marginBottom: 20 }}>
          <span style={{ ...mono(10, INK_DIM), display: 'block', marginBottom: 12 }}>Activity History</span>
          <ActivityTimeline activities={activity} onUpdate={loadDetail} />
        </div>

        {/* Chatter — editable notes thread */}
        <div style={{ borderTop: `1px solid ${LINE}`, paddingTop: 16, marginBottom: 20 }}>
          <span style={{ ...mono(10, INK_DIM), display: 'block', marginBottom: 12 }}>Chatter</span>
          <NotesThread key={`deal-${deal.id}`} entityType="deal" entityId={deal.id} />
        </div>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button onClick={onClose} style={{
            padding: '10px 16px', borderRadius: 6,
            border: `1px solid ${LINE_STRONG}`, background: 'transparent',
            color: INK_MUTE, fontSize: 13, cursor: 'pointer',
          }}>Close</button>
          <button onClick={() => onEdit(deal)} style={{
            padding: '10px 16px', borderRadius: 6,
            border: `1px solid ${LINE_STRONG}`, background: 'transparent',
            color: INK, fontSize: 13, cursor: 'pointer',
          }}>Edit</button>
          {deal.stage !== 'won' && deal.stage !== 'lost' && (
            <>
              <button onClick={() => onStageChange(deal, 'won')} style={{
                padding: '10px 16px', borderRadius: 6,
                background: SAGE, color: ACCENT_INK,
                border: 'none', fontWeight: 500, fontSize: 13, cursor: 'pointer',
                flex: 1,
              }}>Mark Won</button>
              <button onClick={() => onStageChange(deal, 'lost')} style={{
                ...btnDanger,
                padding: '10px 16px', borderRadius: 6, fontSize: 13,
              }}>Mark Lost</button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
