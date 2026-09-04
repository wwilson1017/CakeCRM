/**
 * CakeCRM — confirm dialog host. Mounted once in App.tsx; renders the head
 * of the confirm store's queue as a styled modal.
 */

import { useEffect, useRef, useSyncExternalStore } from 'react';
import { subscribeConfirms, getCurrentConfirm, settleConfirm } from './confirm';
import type { ConfirmOptions } from './confirm';
import {
  BG_ELEV, LINE_STRONG, INK, INK_MUTE, INK_SOFT, ACCENT, ACCENT_INK, CORAL_FILL, ON_STATUS,
  FONT_DISPLAY, FONT_SANS,
  SCRIM, SHADOW,
} from './styles';

export function ConfirmHost() {
  const current = useSyncExternalStore(subscribeConfirms, getCurrentConfirm, () => null);
  return current ? <ConfirmCard key={current.id} options={current.options} /> : null;
}

function ConfirmCard({ options }: { options: ConfirmOptions }) {
  const { title, message, confirmLabel = 'Confirm', cancelLabel = 'Cancel', danger } = options;
  const cancelRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const previouslyFocused = document.activeElement;
    // Danger dialogs focus Cancel so a reflexive Enter can't destroy data;
    // otherwise Confirm gets focus to match native confirm()'s Enter ≈ OK.
    (danger ? cancelRef.current : confirmRef.current)?.focus();

    // Capture phase: runs before any bubble-phase document-level Escape
    // listener, so Escape closes only the confirm — not a popover underneath it.
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        settleConfirm(false);
      } else if (e.key === 'Tab') {
        // Two focusable elements — trap focus by toggling between them.
        e.preventDefault();
        e.stopPropagation();
        (document.activeElement === confirmRef.current ? cancelRef : confirmRef).current?.focus();
      }
    }
    document.addEventListener('keydown', onKeyDown, { capture: true });
    return () => {
      document.removeEventListener('keydown', onKeyDown, { capture: true });
      if (previouslyFocused instanceof HTMLElement && document.contains(previouslyFocused)) {
        previouslyFocused.focus();
      }
    };
  }, [danger]);

  return (
    <div
      onClick={() => settleConfirm(false)}
      style={{
        position: 'fixed', inset: 0, background: SCRIM,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 150, padding: 16,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="crm-confirm-title"
        aria-describedby="crm-confirm-message"
        onClick={e => e.stopPropagation()}
        style={{
          background: BG_ELEV, borderRadius: 6,
          border: `1px solid ${LINE_STRONG}`,
          padding: 28, width: '100%', maxWidth: 420,
          boxShadow: `0 8px 40px ${SHADOW}`,
        }}
      >
        <h2
          id="crm-confirm-title"
          style={{
            fontFamily: FONT_DISPLAY, fontSize: 20, fontWeight: 400,
            letterSpacing: '-0.02em', margin: '0 0 8px', color: INK,
          }}
        >
          {title}
        </h2>
        <p
          id="crm-confirm-message"
          style={{
            fontFamily: FONT_SANS, fontSize: 13, color: INK_SOFT,
            lineHeight: 1.5, margin: '0 0 24px',
          }}
        >
          {message}
        </p>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            ref={cancelRef}
            type="button"
            onClick={() => settleConfirm(false)}
            style={{
              flex: 1, padding: '9px 16px', borderRadius: 4,
              border: `1px solid ${LINE_STRONG}`,
              background: 'transparent', color: INK_MUTE,
              cursor: 'pointer', fontSize: 13, fontFamily: FONT_SANS,
            }}
          >
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            type="button"
            onClick={() => settleConfirm(true)}
            style={{
              flex: 1, padding: '9px 16px', borderRadius: 4,
              // ON_STATUS, not ACCENT_INK, on the danger branch: white is right on the
              // brand red (4.66:1 in both themes) but only 2.96:1 on the red `.dark`
              // lightened for text. ON_STATUS flips per theme; ACCENT keeps white.
              background: danger ? CORAL_FILL : ACCENT,
              color: danger ? ON_STATUS : ACCENT_INK,
              border: 'none', fontWeight: 500, cursor: 'pointer',
              fontSize: 13, fontFamily: FONT_SANS,
            }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
