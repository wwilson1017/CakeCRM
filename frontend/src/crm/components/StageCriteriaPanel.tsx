/**
 * The pipeline stage checklist, three ways (#289, port of upstream #3633 plus editable criteria).
 *
 * - `StageCriteriaContent` — the one body every surface shares.
 * - `StageCriteriaPeek` — the hover quick look on a stage name (desktop only; touch has no hover).
 *   Portaled to <body> and positioned `fixed` from the name's rect, because the board is an
 *   `overflow: auto` scroller that clips any absolutely positioned popover inside it. That clip
 *   is why the pre-#289 checklist expanded INLINE and pushed the column's cards down.
 * - `StageCriteriaPanel` — the PINNED checklist: a NON-modal panel. No backdrop, no scroll lock,
 *   no focus trap, no `aria-modal`, so the board behind keeps scrolling, dragging and opening
 *   deals while a rep works down the list. Admins also edit the stage's criteria here.
 */
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ApiError } from '../../core/api/client';
import { useAuth } from '../../core/auth/AuthContext';
import { confirmDialog } from '../../shared/confirm';
import { toast } from '../../shared/toast';
import {
  INK, INK_MUTE, INK_DIM, LINE, LINE_STRONG, BG_ELEV, SHADOW, FONT_DISPLAY, mono, inputStyle,
  ACCENT_TEXT,
} from '../../shared/styles';
import { btnPrimary, btnSecondary, btnSmall, LAUNCHER_CLEARANCE_PX } from '../styles';
import { STAGE_COLORS } from '../constants';
import { stageLabel } from '../pipelineBoard';
import {
  resetStageCriteria, saveStageCriteria,
  type StageCriteria, type StageCriteriaEntry,
} from '../stageCriteria';

/** Mirrors the server's limits (crm/router.py `StageCriteriaBody`), so the form stops first. */
const MAX_ITEMS = 30;

export function StageCriteriaContent({ criteria }: { criteria: StageCriteria }) {
  return (
    <>
      <p style={{ fontSize: 12, color: INK_MUTE, margin: 0, lineHeight: 1.5 }}>{criteria.summary}</p>
      <p style={{ fontSize: 12, color: INK, margin: '10px 0 6px', fontWeight: 500 }}>
        Criteria to enter this stage:
      </p>
      <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 4 }}>
        {criteria.checklist.map((item, i) => (
          <li key={i} style={{ fontSize: 12, color: INK_MUTE, lineHeight: 1.45, display: 'flex', gap: 6 }}>
            <span style={{ color: INK_DIM, flexShrink: 0 }}>☐</span>{item}
          </li>
        ))}
      </ul>
    </>
  );
}

function StageTitle({ stage }: { stage: string }) {
  return (
    <>
      <span style={{ width: 10, height: 10, borderRadius: '50%', flexShrink: 0, background: STAGE_COLORS[stage]?.fill || INK_DIM }} />
      <span style={{ fontFamily: FONT_DISPLAY, fontSize: 15, color: INK }}>{stageLabel(stage)}</span>
    </>
  );
}

const PEEK_WIDTH = 320;

export function StageCriteriaPeek({ stage, criteria, anchor, onEnter, onLeave }: {
  stage: string;
  criteria: StageCriteria;
  /** The stage name's viewport rect, measured when the hover began. */
  anchor: DOMRect;
  onEnter: () => void;
  onLeave: () => void;
}) {
  // Clamped to the viewport so a right-hand column's peek never runs off screen. It sits flush
  // against the name (no gap), so the pointer can travel into it without the peek closing.
  const left = Math.max(8, Math.min(anchor.left, window.innerWidth - PEEK_WIDTH - 8));
  return createPortal(
    <div
      role="tooltip"
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      style={{
        position: 'fixed', top: anchor.bottom, left, width: PEEK_WIDTH, zIndex: 38,
        paddingTop: 6,
      }}
    >
      <div style={{
        padding: 14, borderRadius: 8, background: BG_ELEV, border: `1px solid ${LINE_STRONG}`,
        boxShadow: `0 8px 40px ${SHADOW}`,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <StageTitle stage={stage} />
        </div>
        <StageCriteriaContent criteria={criteria} />
      </div>
    </div>,
    document.body,
  );
}

/**
 * Is another dialog on screen? The pinned panel is always the LOWEST surface (the deal sheet,
 * every modal and the confirm host sit above it), and the deal sheet closes on Escape without
 * marking the event handled — so without this one Escape would close both.
 */
function anotherDialogOpen(self: HTMLElement | null): boolean {
  return [...document.querySelectorAll<HTMLElement>('[role="dialog"]')]
    .some(el => el !== self && el.getBoundingClientRect().width > 0);
}

const stop = (e: { stopPropagation: () => void }) => e.stopPropagation();

export function StageCriteriaPanel({ entry, isMobile = false, onClose, onSaved }: {
  entry: StageCriteriaEntry;
  isMobile?: boolean;
  onClose: () => void;
  onSaved: (entry: StageCriteriaEntry) => void;
}) {
  // Edit and Reset are admin only — the `adminOnly` rule Settings cards follow
  // (`crm/settingsSections.ts`): both write routes are `require_admin`, so a member is never
  // shown a control that can only 403.
  const canEdit = useAuth().isAdmin;
  const ref = useRef<HTMLDivElement>(null);
  // The draft while editing; null when reading. Click-anywhere and Escape do NOT close the panel
  // while a draft is open — both would throw away typing. Save, Cancel and × are the exits.
  const [draft, setDraft] = useState<{ summary: string; items: string[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const editing = draft !== null;

  const editingRef = useRef(editing);
  useEffect(() => { editingRef.current = editing; }, [editing]);
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented || editingRef.current) return;
      if (anotherDialogOpen(ref.current)) return;
      onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const startEdit = () => setDraft({ summary: entry.summary, items: [...entry.checklist] });
  const setItem = (i: number, v: string) =>
    setDraft(d => d && { ...d, items: d.items.map((x, j) => (j === i ? v : x)) });
  const move = (i: number, by: -1 | 1) => setDraft(d => {
    if (!d) return d;
    const items = [...d.items];
    [items[i], items[i + by]] = [items[i + by], items[i]];
    return { ...d, items };
  });

  const items = draft?.items.map(s => s.trim()) ?? [];
  const valid = !!draft && draft.summary.trim() !== '' && items.length > 0 && items.every(Boolean);

  async function save() {
    if (!draft || !valid || busy) return;
    setBusy(true);
    try {
      onSaved(await saveStageCriteria(entry.stage, { summary: draft.summary.trim(), checklist: items }));
      setDraft(null);
      toast.success(`${stageLabel(entry.stage)} criteria saved`);
    } catch (e) {
      // The draft stays as typed, so a failed save loses nothing.
      toast.error(e instanceof ApiError ? e.detail : 'Could not save the criteria');
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    if (busy) return;
    const ok = await confirmDialog({
      title: `Reset ${stageLabel(entry.stage)} to the standard criteria?`,
      message: 'Your own summary and checklist for this stage are replaced by the standard ones.',
      confirmLabel: 'Reset',
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      onSaved(await resetStageCriteria(entry.stage));
      toast.success(`${stageLabel(entry.stage)} is back to the standard criteria`);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.detail : 'Could not reset the criteria');
    } finally {
      setBusy(false);
    }
  }

  const iconBtn = {
    background: 'none', border: 'none', cursor: 'pointer', color: INK_DIM,
    fontSize: 13, padding: '2px 5px', lineHeight: 1,
  } as const;

  return (
    <div
      ref={ref}
      role="dialog"
      aria-label={`${stageLabel(entry.stage)} stage checklist`}
      onClick={editing ? undefined : onClose}
      style={{
        position: 'fixed',
        // z 38: above the board, below the deal sheet (39), the launcher (40) and every modal
        // (50+), so opening a deal covers the panel rather than closing it.
        zIndex: 38,
        // Clear of the "Ask Baker" pill in the bottom-right corner (and the toasts above it),
        // on both layouts — the same clearance every page scrolls past.
        bottom: LAUNCHER_CLEARANCE_PX,
        ...(isMobile
          ? { left: 12, right: 12, maxHeight: '50vh', borderRadius: 12 }
          : { right: 20, width: 340, maxHeight: '60vh', borderRadius: 10 }),
        overflowY: 'auto', boxSizing: 'border-box', padding: 16,
        background: BG_ELEV, border: `1px solid ${LINE_STRONG}`, boxShadow: `0 8px 40px ${SHADOW}`,
        cursor: editing ? 'auto' : 'pointer',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <StageTitle stage={entry.stage} />
        <span style={{
          ...mono(10, entry.source === 'custom' ? ACCENT_TEXT : INK_DIM),
          border: `1px solid ${LINE}`, borderRadius: 999, padding: '1px 7px',
        }}>{entry.source === 'custom' ? 'Custom' : 'Standard'}</span>
        {/* No handler of its own: the click bubbles to the panel, which closes. While editing
            the panel ignores clicks, so here it closes explicitly (discarding the draft). */}
        <button
          type="button"
          aria-label="Close stage checklist"
          onClick={editing ? onClose : undefined}
          style={{ ...iconBtn, marginLeft: 'auto', fontSize: 18, padding: '0 4px' }}
        >×</button>
      </div>

      {draft ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <label style={{ fontSize: 12, color: INK, fontWeight: 500 }}>
            Summary
            <textarea
              value={draft.summary}
              onChange={e => setDraft({ ...draft, summary: e.target.value })}
              rows={3}
              maxLength={1000}
              style={{ ...inputStyle, marginTop: 4, fontSize: 13, resize: 'vertical' }}
            />
          </label>
          <span style={{ fontSize: 12, color: INK, fontWeight: 500 }}>Criteria to enter this stage</span>
          {draft.items.map((item, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              <input
                value={item}
                onChange={e => setItem(i, e.target.value)}
                maxLength={300}
                aria-label={`Checklist item ${i + 1}`}
                style={{ ...inputStyle, fontSize: 13, padding: '6px 8px' }}
              />
              <button type="button" aria-label={`Move item ${i + 1} up`} disabled={i === 0}
                onClick={() => move(i, -1)} style={{ ...iconBtn, opacity: i === 0 ? 0.3 : 1 }}>↑</button>
              <button type="button" aria-label={`Move item ${i + 1} down`} disabled={i === draft.items.length - 1}
                onClick={() => move(i, 1)} style={{ ...iconBtn, opacity: i === draft.items.length - 1 ? 0.3 : 1 }}>↓</button>
              <button type="button" aria-label={`Remove item ${i + 1}`} disabled={draft.items.length === 1}
                onClick={() => setDraft({ ...draft, items: draft.items.filter((_, j) => j !== i) })}
                style={{ ...iconBtn, opacity: draft.items.length === 1 ? 0.3 : 1 }}>×</button>
            </div>
          ))}
          <button
            type="button"
            disabled={draft.items.length >= MAX_ITEMS}
            onClick={() => setDraft({ ...draft, items: [...draft.items, ''] })}
            style={{ ...btnSecondary, ...btnSmall, alignSelf: 'flex-start' }}
          >Add item</button>
          <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
            <button type="button" onClick={save} disabled={!valid || busy}
              style={{ ...btnPrimary, ...btnSmall, opacity: !valid || busy ? 0.5 : 1 }}>
              {busy ? 'Saving…' : 'Save'}
            </button>
            <button type="button" onClick={() => setDraft(null)} disabled={busy}
              style={{ ...btnSecondary, ...btnSmall }}>Cancel</button>
          </div>
        </div>
      ) : (
        <>
          <StageCriteriaContent criteria={entry} />
          {canEdit && (
            <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
              <button type="button" onClick={e => { stop(e); startEdit(); }} disabled={busy}
                style={{ ...btnSecondary, ...btnSmall }}>Edit</button>
              {entry.source === 'custom' && (
                <button type="button" onClick={e => { stop(e); void reset(); }} disabled={busy}
                  style={{ ...btnSecondary, ...btnSmall }}>Reset to standard</button>
              )}
            </div>
          )}
          <p style={{ fontSize: 11, color: INK_DIM, margin: '12px 0 0' }}>
            {isMobile ? 'Tap' : 'Click'} anywhere to close
          </p>
        </>
      )}
    </div>
  );
}
