/**
 * A larger editing window for one long text field (#305, from PlaybackCRM #36): the todo description.
 *
 * The todo modal's description box is two lines, right for most todos and cramped for
 * the ones that hold a brief. This opens the same text in a window-sized textarea and
 * hands the result back on Save; Cancel (or Escape) discards the draft and leaves the
 * field as it was. Cmd/Ctrl+Enter saves, under `composerKeyAction`'s rule so the chord shares
 * the IME and AltGr guards every multi-line box in the app has.
 *
 * Rendered in place, not through a portal: the no-login todo app reaches this through
 * TodoEditSheet and its download keeps `react-dom` out (`bootSplit.test.ts`). A fixed,
 * inset-0 child of the host's own fixed overlay still covers the viewport and sits above the
 * form within that overlay's stacking context, which is all it needs. Clicks on its backdrop
 * stop here, so the host overlay's own click-to-close never fires through it.
 */

import { useEffect, useRef, useState } from 'react';

import {
  ACCENT, ACCENT_INK, BG_ELEV, FONT_DISPLAY, INK, INK_DIM, INK_MUTE, LINE_STRONG, SCRIM, SHADOW,
  inputStyle, mono,
} from './styles';

// Lives in shared/ (not crm/) and imports nothing from crm/, because the no-login
// todo app reaches it through TodoEditSheet and `bootSplit.test.ts` keeps CRM code out of that
// download. The chord check is `composerKeyAction`'s rule, restated: Enter with Cmd/Ctrl, never
// mid-IME composition and never with Alt (Windows synthesises AltGr as Ctrl+Alt).
function isSubmitChord(e: { key: string; metaKey: boolean; ctrlKey: boolean; altKey: boolean; isComposing: boolean }) {
  return e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !e.altKey && !e.isComposing;
}
const button = { padding: '8px 14px', borderRadius: 4, fontSize: 13, cursor: 'pointer', fontFamily: FONT_DISPLAY } as const;
const btnPrimary = { ...button, background: ACCENT, color: ACCENT_INK, border: 'none' } as const;
const btnSecondary = { ...button, background: 'transparent', color: INK_MUTE, border: `1px solid ${LINE_STRONG}` } as const;

export interface BigTextEditorProps {
  title: string;
  value: string;
  onSave: (text: string) => void;
  onCancel: () => void;
}

export function BigTextEditor({ title, value, onSave, onCancel }: BigTextEditorProps) {
  const [text, setText] = useState(value);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const downOnBackdrop = useRef(false);
  const onCancelRef = useRef(onCancel);
  useEffect(() => { onCancelRef.current = onCancel; });

  useEffect(() => {
    const previouslyFocused = document.activeElement;
    const el = textareaRef.current;
    if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        onCancelRef.current();
        return;
      }
      // Tab stays inside: the form underneath is still a live DOM subtree, so an untrapped
      // Shift+Tab would land on its controls behind this `aria-modal` dialog.
      if (e.key !== 'Tab') return;
      const focusable = [...(dialogRef.current?.querySelectorAll<HTMLElement>('textarea, button') ?? [])]
        .filter(el => !(el as HTMLButtonElement).disabled);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      const inside = active instanceof Node && dialogRef.current?.contains(active);
      if (!inside) {
        e.preventDefault(); e.stopPropagation(); (e.shiftKey ? last : first).focus();
      } else if (e.shiftKey && active === first) {
        e.preventDefault(); e.stopPropagation(); last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault(); e.stopPropagation(); first.focus();
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

  return (
    <div
      // Only a click that starts AND ends on the backdrop cancels: a drag that starts in the
      // textarea and ends outside is targeted at this wrapper too (the browser sends the click
      // to the common ancestor), so where the press began is recorded and checked.
      onClick={e => {
        e.stopPropagation();
        if (e.target === e.currentTarget && downOnBackdrop.current) onCancel();
        downOnBackdrop.current = false;
      }}
      onMouseDown={e => { e.stopPropagation(); downOnBackdrop.current = e.target === e.currentTarget; }}
      style={{
        position: 'fixed', inset: 0, background: SCRIM, zIndex: 150,
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="big-text-editor-title"
        onClick={e => e.stopPropagation()}
        style={{
          background: BG_ELEV, borderRadius: 6, boxShadow: SHADOW,
          border: `1px solid ${LINE_STRONG}`, padding: 24,
          width: '100%', maxWidth: 760, display: 'flex', flexDirection: 'column', gap: 12,
          maxHeight: '90dvh',
        }}
      >
        <h2
          id="big-text-editor-title"
          style={{ fontFamily: FONT_DISPLAY, fontSize: 18, fontWeight: 400, color: INK, margin: 0 }}
        >{title}</h2>
        <textarea
          ref={textareaRef}
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={e => {
            if (isSubmitChord({
              key: e.key, metaKey: e.metaKey, ctrlKey: e.ctrlKey, altKey: e.altKey,
              isComposing: e.nativeEvent.isComposing,
            })) { e.preventDefault(); onSave(text); }
          }}
          style={{
            ...inputStyle, width: '100%', fontSize: 14, lineHeight: 1.5,
            minHeight: '50dvh', resize: 'vertical',
          }}
        />
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ ...mono(10), color: INK_DIM, flex: 1 }}>Cmd/Ctrl+Enter to save · Esc to cancel</span>
          <button type="button" onClick={onCancel} style={btnSecondary}>Cancel</button>
          <button type="button" onClick={() => onSave(text)} style={btnPrimary}>Save</button>
        </div>
      </div>
    </div>
  );
}
