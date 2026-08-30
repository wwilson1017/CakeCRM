import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../../core/api/client';
import type { CrmDeal, CrmActivity } from '../../core/types';
import { STAGE_COLORS } from '../constants';
import { mono, INK, INK_MUTE, INK_DIM, LINE, LINE_STRONG, ACCENT_INK, GOLD, SAGE, FONT_DISPLAY } from '../../shared/styles';
import { modalOverlay, modalContent, mobileDragHandle, btnDanger } from '../styles';
import { toast } from '../../shared/toast';
import { ActivityTimeline } from './ActivityTimeline';
import { NotesThread } from './NotesThread';
import { ScorePill } from './badges';
import { AiTouchDetail } from './AiTouchDetail';
import { ProvenanceBadge } from './ProvenanceBadge';
import { useProvenance } from '../useProvenance';
import { CustomFieldsSection } from './CustomFieldsSection';
import { LostReasonModal } from './LostReasonModal';
import { OwnerName } from './OwnerName';
import { usePublishActiveRecord } from '../RecordContext';

interface DealDetailSheetProps {
  deal: CrmDeal;
  isMobile: boolean;
  onClose: () => void;
  onEdit: (deal: CrmDeal) => void;
  /** `lostReason` is present ONLY for a Mark Lost taken through the reason dialog — a
   *  string, possibly empty. Every other move leaves it undefined, which is what tells the
   *  host to use the plain stage PUT rather than the mark-lost verb (see dealStageWrite).
   *
   *  May return a promise; the sheet awaits it to keep the close-out buttons disabled for
   *  the duration. A host that resolves synchronously (Pipeline, which closes the sheet
   *  itself) is unaffected. */
  onStageChange: (deal: CrmDeal, stage: string, lostReason?: string) => void | Promise<void>;
  /** A deal was un-archived here (issue #83). Receives the row the server returned so the
   *  host can patch it in place — a silent refetch can fail invisibly, which would leave
   *  the board showing a deal as archived after a restore that actually happened. */
  onRestored?: (deal: CrmDeal) => void;
}

export function DealDetailSheet({ deal, isMobile, onClose, onEdit, onStageChange, onRestored }: DealDetailSheetProps) {
  // Publish this deal as the open record while the sheet is mounted (issue #14).
  // Deals have no route, so this IS the deal open/close signal for both Pipeline
  // and Dashboard — no edits to either page.
  usePublishActiveRecord('deal', deal.id, deal.title);

  // The pipeline passes a plain list-row deal (no activity). Fetch the detail so
  // the sheet can show the activity timeline alongside the chatter thread.
  const [activity, setActivity] = useState<CrmActivity[]>(deal.activity || []);
  // Read the touch count from re-fetchable state (not the frozen list-row prop): loadDetail
  // refreshes it on open and after activity mutations, so the sheet shows the latest STORED
  // count. The recompute itself is async (a background LLM call, seconds after a note), so a
  // freshly-triggered count lands on the next fetch/navigation — accepted eventual
  // consistency for an estimate nudge (see the PR's accepted-limitations note).
  const [touchCount, setTouchCount] = useState<number | null | undefined>(deal.ai_touch_count);
  // Same rationale for lead_score (issue #18): render the re-fetchable value, not the frozen
  // list-row prop, so an in-sheet activity mutation (or the daily refresh / another client)
  // updates the pill on the next loadDetail rather than showing a stale score until close.
  const [leadScore, setLeadScore] = useState<number | null | undefined>(deal.lead_score);
  // Archived state comes from the re-fetchable detail too (issue #83), not only the frozen
  // list-row prop: the assistant can archive a deal between the board's load and this sheet
  // opening, and `get_deal` resolves an archived deal by design. Seeded from the prop so
  // the banner is right on first paint.
  //
  // Accepted limitation: between first paint and `loadDetail` resolving, the archive-gated
  // actions still reflect the prop, so a deal archived externally shows Mark Won/Lost for
  // that window. NOT gated on the fetch — every other field in this sheet renders from the
  // frozen prop until the detail lands (`stage` gates those same buttons the same way), so
  // gating only these would buy a sub-second race at the cost of buttons that pop in after
  // the sheet opens. The worst outcome in that window is a refused write, not a bad one.
  const [archivedAt, setArchivedAt] = useState<string | null | undefined>(deal.archived_at);
  // Stage rides the same re-fetchable state as archivedAt above, and for a sharper reason
  // than staleness: a close whose response was lost may ALREADY have committed. The catch
  // path re-enables the close-out buttons, so reading the frozen prop would keep offering
  // Mark Lost on a deal the server has already marked lost — and a retry appends a second
  // "Deal lost —" note, since mark_deal_lost writes one on every call that finds the deal.
  const [stage, setStage] = useState(deal.stage);
  const [restoring, setRestoring] = useState(false);
  // Mark Lost opens the reason dialog instead of closing the deal immediately (issue #128).
  const [askingLostReason, setAskingLostReason] = useState(false);
  // A close-out is in flight. Needed because the two hosts dismiss differently: Pipeline
  // clears its selection synchronously, but CrmDashboardPage awaits the write and KEEPS the
  // sheet open on failure so the user can retry — so without this the buttons stay live
  // during the request, and a second Mark Lost writes a second "Deal lost —" timeline note
  // (mark_deal_lost appends one on every call that finds the deal, no-op write included).
  // The modal's own latch cannot cover this: it unmounts as soon as the first one confirms.
  const [closing, setClosing] = useState(false);
  // Disable the close-out pair while EITHER a write is in flight or the reason dialog
  // is open. The dialog half is defence in depth behind the modal's focus trap: the
  // sheet stays a live DOM subtree underneath, and Mark Won sitting one stray Tab away
  // from an open Mark Lost dialog is a wrong write, not just an a11y lapse.
  const closeOutDisabled = closing || askingLostReason;
  const { byField, confirm, confirming } = useProvenance('deal', deal.id);
  const badge = (f: string) => (
    <ProvenanceBadge prov={byField[f]} onConfirm={() => confirm(f)} confirming={confirming === f} />
  );
  // Whether this sheet is still on screen when an awaited write settles — see closeOut.
  const mountedRef = useRef(true);
  useEffect(() => () => { mountedRef.current = false; }, []);
  const reqRef = useRef(0);
  const loadDetail = useCallback(async () => {
    const reqId = ++reqRef.current;
    try {
      const detail = await api<CrmDeal>(`/api/crm/deals/${deal.id}`);
      if (reqId !== reqRef.current) return;
      setActivity(detail.activity || []);
      setTouchCount(detail.ai_touch_count);
      setLeadScore(detail.lead_score);
      setArchivedAt(detail.archived_at);
      setStage(detail.stage);
    } catch {
      // Non-fatal: the sheet still shows deal fields + chatter; leave activity as-is.
    }
  }, [deal.id]);

  useEffect(() => { queueMicrotask(loadDetail); }, [loadDetail]);

  // Restore (issue #83). Self-contained POST — the same shape NotesThread uses for the
  // chatter archive/unarchive pair — but the authoritative row goes UP to the host rather
  // than being thrown away in favour of a refetch that can fail silently. `restoring` is
  // reset only on failure: on success the host unmounts this sheet.
  async function restoreDeal() {
    setRestoring(true);
    try {
      const restored = await api<CrmDeal>(`/api/crm/deals/${deal.id}/restore`, { method: 'POST' });
      setArchivedAt(null);
      onRestored?.(restored);
    } catch {
      toast.error('Failed to restore deal.');
    } finally {
      // Reset on BOTH paths. A host that closes the sheet on `onRestored` unmounts this
      // anyway, but `onRestored` is optional and the banner renders on every host — leaving
      // `restoring` stuck true would strand the button on "Restoring…" for any host that
      // keeps the sheet open.
      setRestoring(false);
    }
  }

  // Close the deal out (issue #128). Awaits the host so both buttons stay `disabled` for
  // the whole write — that attribute IS the re-entry guard, and it covers the modal path
  // too, since the only way back into the dialog is the Mark Lost button.
  //
  // Resets on BOTH paths for the same reason `restoreDeal` does: the dashboard keeps this
  // sheet open when the write fails, so a latched-true flag would leave the user unable to
  // retry the close they just watched fail.
  async function closeOut(toStage: string, lostReason?: string) {
    setClosing(true);
    try {
      await onStageChange(deal, toStage, lostReason);
    } finally {
      setClosing(false);
      // Still mounted means the host did NOT dismiss us — which for the dashboard host is
      // its failure path (it swallows the error itself, so nothing rejects here). The
      // outcome of that write is genuinely UNKNOWN: a dropped connection or a 5xx can
      // arrive after the server already committed. So reconcile against the server before
      // offering a retry — a close that did land re-reads as stage 'lost' and these
      // buttons disappear, instead of inviting a second mark_deal_lost.
      if (mountedRef.current) void loadDetail();
    }
  }

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
            display: 'inline-flex', alignItems: 'center', gap: 8, flexShrink: 0, marginLeft: 12,
          }}>
            <span style={{ fontFamily: FONT_DISPLAY, fontSize: 20, color: GOLD }}>
              ${deal.value.toLocaleString()}
            </span>
            {badge('value')}
          </span>
        </div>

        <div style={{
          display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
          fontSize: 12, color: INK_MUTE, marginBottom: 16,
        }}>
          <span style={{ textTransform: 'capitalize' }}>
            Stage: <span style={{ color: STAGE_COLORS[deal.stage]?.color || INK }}>{deal.stage}</span>
          </span>
          {badge('stage')}
        </div>

        {/* issue #56: the touch count and, on demand, every event behind it. Replaces the
            bare pill that used to sit in the stage row above. Renders nothing when the
            count is NULL, so a keyless install sees no affordance at all. */}
        <AiTouchDetail dealId={deal.id} count={touchCount} />

        {deal.notes && (
          <p style={{
            fontSize: 14, color: INK_MUTE, marginBottom: 16, lineHeight: 1.5,
            whiteSpace: 'pre-wrap',
          }}>
            {deal.notes} {badge('notes')}
          </p>
        )}

        {/* Why the deal was lost (issue #22). Cleared automatically if the deal is
            reopened, so this only ever shows on a currently-lost deal.
            `pre-wrap` because #128 made the reason multi-line: this is the one surface
            that shows it, so collapsing the newlines here would deliver half the feature
            (the same defect the blueprint hit on note bodies). `deal.notes` above is a
            multi-line textarea too and had the identical bug. */}
        {deal.lost_reason && (
          <p style={{
            fontSize: 13, color: INK_MUTE, marginBottom: 16, lineHeight: 1.5,
            whiteSpace: 'pre-wrap',
          }}>
            <span style={{ ...mono(10), color: INK_DIM, marginRight: 6 }}>LOST REASON</span>
            {deal.lost_reason} {badge('lost_reason')}
          </p>
        )}

        {/* The archived-deal banner #22 Phase 1 deliberately left out, now that the
            pipeline's Archived facet (issue #83) makes an archived deal reachable. This is
            the ONLY way back on an install with no AI provider: the assistant's
            crm_archive_deal(archived=false) needs a key, this doesn't.
            Neutral dashed border rather than a danger tint — archived is a state, not a
            problem. The copy is deliberately narrow: archived deals leave pipeline totals
            and deal rollups, but their history stays in the activity feed by design. */}
        {archivedAt && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
            border: `1px dashed ${LINE_STRONG}`, borderRadius: 6,
            padding: '10px 12px', marginBottom: 16,
          }}>
            <span style={{ ...mono(10), color: INK_DIM }}>ARCHIVED</span>
            <span style={{ fontSize: 13, color: INK_MUTE, flex: 1, lineHeight: 1.5 }}>
              Archived {new Date(archivedAt).toLocaleDateString()} — excluded from pipeline
              totals and deal rollups.
            </span>
            <button
              onClick={restoreDeal}
              disabled={restoring}
              style={{
                padding: '8px 14px', borderRadius: 6, border: `1px solid ${LINE_STRONG}`,
                background: 'transparent', color: INK, fontSize: 13,
                cursor: restoring ? 'default' : 'pointer', opacity: restoring ? 0.5 : 1,
              }}
            >{restoring ? 'Restoring…' : 'Restore'}</button>
          </div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 20 }}>
          {/* Rendered unconditionally, unlike every other row here (issue #128): an
              unassigned owner is a real state, and hiding the row is what makes
              "unassigned" indistinguishable from "not displayed". */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ ...mono(10), color: INK_DIM }}>Owner</span>
            <OwnerName ownerId={deal.owner_id} />
          </div>
          {deal.contact_name && (
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ ...mono(10), color: INK_DIM }}>Contact</span>
              <span style={{ fontSize: 13, color: INK }}>{deal.contact_name}</span>
            </div>
          )}
          {deal.probability > 0 && (
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ ...mono(10), color: INK_DIM }}>Probability</span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 13, color: INK }}>{deal.probability}%</span>
                {badge('probability')}
              </span>
            </div>
          )}
          {leadScore != null && (
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ ...mono(10), color: INK_DIM }}>Lead Score</span>
              <ScorePill score={leadScore} />
            </div>
          )}
          {deal.expected_close_date && (
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ ...mono(10), color: INK_DIM }}>Expected Close</span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 13, color: INK }}>{deal.expected_close_date}</span>
                {badge('expected_close_date')}
              </span>
            </div>
          )}
        </div>

        {/* Custom fields — renders nothing when no deal fields are defined */}
        <CustomFieldsSection
          key={`deal-${deal.id}`}
          entityType="deal"
          entityId={deal.id}
          sectionStyle={{ borderTop: `1px solid ${LINE}`, paddingTop: 16, marginBottom: 20 }}
        />

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
          {/* Hand the edit form the RE-FETCHED archived state, not the frozen board row:
              otherwise a deal archived after the board loaded shows the banner here while
              DealForm still sees `archived_at: null`, leaves Stage editable, and lets the
              user compose an update the server will reject wholesale. */}
          <button onClick={() => onEdit({ ...deal, archived_at: archivedAt })} style={{
            padding: '10px 16px', borderRadius: 6,
            border: `1px solid ${LINE_STRONG}`, background: 'transparent',
            color: INK, fontSize: 13, cursor: 'pointer',
          }}>Edit</button>
          {/* Hidden on an archived deal (issue #83): the server refuses a stage change on
              one (`_classify_deal_update` raises → 400) and the caller's catch reports a
              generic failure, so these would be a dead end. Restore first. Edit stays —
              editing an archived deal's other fields is legal. */}
          {!archivedAt && stage !== 'won' && stage !== 'lost' && (
            <>
              <button onClick={() => void closeOut('won')} disabled={closeOutDisabled} style={{
                padding: '10px 16px', borderRadius: 6,
                background: SAGE, color: ACCENT_INK,
                border: 'none', fontWeight: 500, fontSize: 13,
                cursor: closeOutDisabled ? 'default' : 'pointer',
                opacity: closeOutDisabled ? 0.5 : 1,
                flex: 1,
              }}>Mark Won</button>
              {/* Ask for the reason first (issue #128). `lost_reason` has no other human
                  writer — it is excluded from _DEAL_USER_WRITABLE, so before this the
                  field could be read on this very sheet but only ever written by the
                  assistant. */}
              <button onClick={() => setAskingLostReason(true)} disabled={closeOutDisabled} style={{
                ...btnDanger,
                padding: '10px 16px', borderRadius: 6, fontSize: 13,
                cursor: closeOutDisabled ? 'default' : 'pointer',
                opacity: closeOutDisabled ? 0.5 : 1,
              }}>Mark Lost</button>
            </>
          )}
        </div>

        {askingLostReason && (
          <LostReasonModal
            dealTitle={deal.title}
            onCancel={() => setAskingLostReason(false)}
            onConfirm={reason => {
              setAskingLostReason(false);
              // Always a string, never undefined — that is what routes this to the
              // mark-lost verb even when the rep left the box empty.
              void closeOut('lost', reason);
            }}
          />
        )}
      </div>
    </div>
  );
}
