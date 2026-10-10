/**
 * Mark-as-Won "Closed on" dialog (#279).
 *
 * The forecast is a guess; the day a deal closed is a fact, and reps record wins late. So
 * Mark Won asks which day it actually closed: default today, earlier days allowed, a future
 * day refused (typing bypasses the input's `max`, hence the explicit check). "Today" is the
 * viewer's local day; the server refuses anything after the INSTALL's day
 * (`service.resolve_closed_on`), so the two agree when the team shares the install's zone.
 *
 * The shell — portal, capture-phase Escape, Tab trap, focus restore, submit-once latch — is
 * `LostReasonModal`'s, for the reasons that file's header gives.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { closedOnIsValid } from '../closedOn';
import { ymd } from '../pipelineFilters';
import {
  BG_ELEV, FONT_DISPLAY, INK, INK_DIM, INK_MUTE, LINE_STRONG,
  SCRIM, SHADOW, inputStyle, mono,
} from '../../shared/styles';
import { btnPrimary, btnSecondary } from '../styles';

export interface ClosedOnModalProps {
  dealTitle: string;
  /** Called at most once with the chosen `YYYY-MM-DD` day. */
  onConfirm: (closedOn: string) => void;
  onCancel: () => void;
}

export function ClosedOnModal({ dealTitle, onConfirm, onCancel }: ClosedOnModalProps) {
  const [today] = useState(() => ymd(new Date()));
  const [day, setDay] = useState(today);
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const submitted = useRef(false);
  const onCancelRef = useRef(onCancel);
  useEffect(() => { onCancelRef.current = onCancel; });

  const valid = closedOnIsValid(day, today);
  const confirm = useCallback(() => {
    if (submitted.current || !valid) return;
    submitted.current = true;
    onConfirm(day);
  }, [onConfirm, day, valid]);

  useEffect(() => {
    const previouslyFocused = document.activeElement;
    inputRef.current?.focus();
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        onCancelRef.current();
        return;
      }
      if (e.key !== 'Tab') return;
      const focusable = [...(dialogRef.current?.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      ) ?? [])].filter(el => !(el as HTMLButtonElement).disabled);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      const inside = active instanceof Node && dialogRef.current?.contains(active);
      if (!inside) {
        e.preventDefault();
        e.stopPropagation();
        (e.shiftKey ? last : first).focus();
      } else if (e.shiftKey && active === first) {
        e.preventDefault();
        e.stopPropagation();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        e.stopPropagation();
        first.focus();
      }
    }
    document.addEventListener('keydown', onKeyDown, { capture: true });
    return () => {
      document.removeEventListener('keydown', onKeyDown, { capture: true });
      if (previouslyFocused instanceof HTMLElement && document.contains(previouslyFocused)) {
        previouslyFocused.focus();
      }
    };
  }, []);

  return createPortal(
    <div
      onClick={onCancel}
      style={{
        position: 'fixed', inset: 0, background: SCRIM,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 150, padding: 16,
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="crm-closed-on-title"
        onClick={e => e.stopPropagation()}
        style={{
          background: BG_ELEV, borderRadius: 6, boxShadow: SHADOW,
          border: `1px solid ${LINE_STRONG}`,
          padding: 24, width: '100%', maxWidth: 420,
          maxHeight: '85dvh', overflowY: 'auto',
        }}
      >
        <h2
          id="crm-closed-on-title"
          style={{
            fontFamily: FONT_DISPLAY, fontSize: 18, fontWeight: 400,
            color: INK, margin: '0 0 4px',
          }}
        >When did this close?</h2>
        <p style={{ fontSize: 13, color: INK_MUTE, margin: '0 0 16px', lineHeight: 1.5 }}>
          {dealTitle}
        </p>

        <label
          htmlFor="crm-closed-on-input"
          style={{ ...mono(10), color: INK_DIM, display: 'block', marginBottom: 6 }}
        >Closed on</label>
        <input
          id="crm-closed-on-input"
          ref={inputRef}
          type="date"
          value={day}
          max={today}
          required
          onChange={e => setDay(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); confirm(); } }}
          style={{ ...inputStyle, width: '100%', fontSize: 13 }}
        />
        {day > today && (
          <p role="alert" style={{ fontSize: 12, color: INK_MUTE, margin: '6px 0 0' }}>
            Closed on cannot be in the future.
          </p>
        )}

        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'flex-end',
          gap: 8, marginTop: 16, flexWrap: 'wrap',
        }}>
          <button
            type="button"
            onClick={onCancel}
            style={{ ...btnSecondary, padding: '8px 14px', fontSize: 13 }}
          >Cancel</button>
          <button
            type="button"
            onClick={confirm}
            disabled={!valid}
            style={{
              ...btnPrimary, padding: '8px 14px', fontSize: 13,
              opacity: valid ? 1 : 0.5, cursor: valid ? 'pointer' : 'default',
            }}
          >Confirm</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
