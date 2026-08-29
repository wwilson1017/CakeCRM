/**
 * Mark-as-Lost reason dialog (issue #128).
 *
 * Ported from the blueprint CRM's `LostReasonModal`, restyled onto CakeCRM's inline theme
 * tokens (the blueprint's Tailwind `bg-cream`/`text-charcoal` classes were removed by #54).
 * Two deliberate divergences from it:
 *
 *  - The blueprint checks `e.key === 'Enter' && (e.metaKey || e.ctrlKey)` inline. This reuses
 *    `composerKeyAction` instead, so the chord inherits the two guards #57 established for
 *    the note composer: `isComposing` off the NATIVE event (an IME uses Enter to commit its
 *    candidate, and React's synthetic event does not carry the flag), and `altKey`
 *    disqualifying the chord (Windows synthesizes AltGr as Ctrl+Alt). One definition of
 *    "what Enter means in a multi-line box" for the whole app.
 *  - The blueprint's field is unbounded; this one carries `maxLength` because
 *    `service.mark_deal_lost` truncates at MAX_LOST_REASON and the route rejects past it.
 *
 * It renders through a PORTAL, which is not decoration: `DealDetailSheet`'s root sets
 * `zIndex: 39` (desktop) so the assistant launcher can sit above it, and that establishes a
 * stacking context — a descendant at any z-index stays trapped beneath the launcher. The
 * portal lifts the overlay to `document.body` at `ConfirmHost`'s z-index instead. It stays a
 * React CHILD of the sheet, because React propagates events along the React tree rather than
 * the DOM tree, and the sheet's inner `onClick={e => e.stopPropagation()}` is what keeps a
 * click in here from reaching the backdrop that closes the sheet.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { composerKeyAction } from '../chatterComposer';
import { MAX_LOST_REASON } from '../constants';
import {
  BG_ELEV, FONT_DISPLAY, INK, INK_DIM, INK_MUTE, LINE_STRONG,
  SCRIM, SHADOW, inputStyle, mono,
} from '../../shared/styles';
import { btnDanger, btnSecondary } from '../styles';

export interface LostReasonModalProps {
  dealTitle: string;
  /** Called at most once with the typed reason — `''` when the user wrote nothing. */
  onConfirm: (reason: string) => void;
  onCancel: () => void;
}

export function LostReasonModal({ dealTitle, onConfirm, onCancel }: LostReasonModalProps) {
  const [reason, setReason] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  // Synchronous latch, deliberately a ref and not the `submitting` state: a held
  // Cmd/Ctrl+Enter (or a double-click) fires again before React re-renders, and
  // `mark_deal_lost` appends its "Deal lost —" note on every call that finds the deal —
  // including a repeat that changes no column, since `_write_deal_update` returns True for
  // a no-op. So a second submit writes a second note.
  const submitted = useRef(false);

  const confirm = useCallback(() => {
    if (submitted.current) return;
    submitted.current = true;
    onConfirm(reason);
  }, [onConfirm, reason]);

  useEffect(() => {
    const previouslyFocused = document.activeElement;
    textareaRef.current?.focus();

    // Capture phase, like ConfirmHost: Escape closes THIS dialog and nothing underneath.
    // (DealDetailSheet has no Escape handling of its own today, so this is purely
    // additive — but stopping propagation is what keeps it that way if one is added.)
    //
    // Tab is trapped for the same reason ConfirmHost traps it, and here it is more than an
    // a11y nicety: `aria-modal` promises the background is inert, but the sheet underneath
    // is a live DOM subtree, so a Shift+Tab out of this dialog lands on its Mark Won button
    // — one keystroke from closing the deal the OPPOSITE way while the Lost dialog is still
    // open. ConfirmHost can toggle between two known buttons; this dialog has a textarea
    // too, so cycle the real focusable list instead.
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        onCancel();
        return;
      }
      if (e.key !== 'Tab') return;
      const focusable = [...(dialogRef.current?.querySelectorAll<HTMLElement>(
        'textarea, button, [href], input, select, [tabindex]:not([tabindex="-1"])',
      ) ?? [])].filter(el => !el.hasAttribute('disabled'));
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      // Also catches focus already sitting OUTSIDE the dialog, which is how it would
      // otherwise stay stuck on the sheet behind once it escaped.
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
  }, [onCancel]);

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
        aria-labelledby="crm-lost-reason-title"
        onClick={e => e.stopPropagation()}
        style={{
          background: BG_ELEV, borderRadius: 6, boxShadow: SHADOW,
          border: `1px solid ${LINE_STRONG}`,
          padding: 24, width: '100%', maxWidth: 420,
          maxHeight: '85dvh', overflowY: 'auto',
        }}
      >
        <h2
          id="crm-lost-reason-title"
          style={{
            fontFamily: FONT_DISPLAY, fontSize: 18, fontWeight: 400,
            color: INK, margin: '0 0 4px',
          }}
        >Mark as Lost</h2>
        <p style={{ fontSize: 13, color: INK_MUTE, margin: '0 0 16px', lineHeight: 1.5 }}>
          {dealTitle}
        </p>

        <label
          htmlFor="crm-lost-reason-input"
          style={{ ...mono(10), color: INK_DIM, display: 'block', marginBottom: 6 }}
        >Reason (optional)</label>
        <textarea
          id="crm-lost-reason-input"
          ref={textareaRef}
          value={reason}
          onChange={e => setReason(e.target.value)}
          // Enter inserts a newline — reasons are prose, and a rep starting a second
          // sentence must not close the deal mid-thought.
          onKeyDown={e => {
            if (composerKeyAction({
              key: e.key, metaKey: e.metaKey, ctrlKey: e.ctrlKey, altKey: e.altKey,
              isComposing: e.nativeEvent.isComposing,
            }) === 'submit') { e.preventDefault(); confirm(); }
          }}
          rows={3}
          maxLength={MAX_LOST_REASON}
          placeholder="e.g. Budget constraints, chose a competitor"
          style={{
            ...inputStyle, width: '100%', fontSize: 13, lineHeight: 1.5,
            resize: 'vertical', minHeight: 72,
          }}
        />

        <div style={{
          display: 'flex', alignItems: 'center', gap: 8, marginTop: 16, flexWrap: 'wrap',
        }}>
          <span style={{ ...mono(10), color: INK_DIM, flex: 1 }}>
            Cmd/Ctrl+Enter to submit
          </span>
          <button
            type="button"
            onClick={onCancel}
            style={{ ...btnSecondary, padding: '8px 14px', fontSize: 13 }}
          >Cancel</button>
          <button
            type="button"
            onClick={confirm}
            style={{ ...btnDanger, padding: '8px 14px', fontSize: 13 }}
          >Mark Lost</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
