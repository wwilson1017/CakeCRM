/**
 * AssistantLauncher — the persistent assistant affordance (issue #9).
 *
 * The assistant is a persistent affordance, not the home page: a fixed
 * bottom-LEFT button always present in the CRM shell (bottom-left keeps it clear
 * of the bottom-right toast column). It degrades gracefully with zero AI keys —
 * never an error:
 *   • aiReady === null   → unknown/loading: rendered but inert.
 *   • aiReady === false  → no key: routes to /setup ("hire your assistant").
 *   • aiReady === true   → opens a full-height left slide-over DRAWER whose body
 *                          is the assistant chat surface (AssistantPanelBody,
 *                          issue #4). The drawer is context-aware (issue #14): the
 *                          CRM record open behind it (via RecordContext) is passed
 *                          in as recordContext for record-aware quick actions and
 *                          per-turn context injection. It stays mounted whenever AI
 *                          is ready (hidden via transform when closed) so live chat
 *                          state survives open/close.
 */

import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AssistantPanelBody } from '../../assistant';
import { IconBot, IconX } from '../../shared/icons';
import {
  INK, INK_MUTE, LINE, BG_CARD, ACCENT, ACCENT_TEXT, ACCENT_INK, FONT_DISPLAY,
  SCRIM, SHADOW,
} from '../../shared/styles';
import { useActiveRecord } from '../RecordContext';

export function AssistantLauncher({ aiReady }: { aiReady: boolean | null }) {
  const navigate = useNavigate();
  const { record } = useActiveRecord();
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const loading = aiReady === null;
  const ready = aiReady === true;

  // Dialog dismissal: Escape (returns focus to the button) and outside-click (a
  // scrim click is "outside" the panel/button, so it closes via the same handler).
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') { setOpen(false); btnRef.current?.focus(); }
    }
    function onDown(e: MouseEvent) {
      const t = e.target as Node;
      if (panelRef.current?.contains(t) || btnRef.current?.contains(t)) return;
      // The scrim covers the viewport, so any "outside" click is on it — restore
      // focus to the launcher (the drawer we're closing may hold focus).
      setOpen(false);
      btnRef.current?.focus();
    }
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onDown);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onDown);
    };
  }, [open]);

  // Move focus into the drawer on open; Escape already returns it to the button.
  useEffect(() => {
    if (open) panelRef.current?.focus();
  }, [open]);

  // Focus containment: the scrim makes the drawer pointer-modal, so keep keyboard
  // focus inside it too (wrap Tab / Shift+Tab at the ends) — otherwise a keyboard user
  // could Tab into scrim-obscured page controls. Paired with aria-modal on the dialog.
  useEffect(() => {
    if (!open) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== 'Tab') return;
      const panel = panelRef.current;
      if (!panel) return;
      const focusables = panel.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (focusables.length === 0) { e.preventDefault(); panel.focus(); return; }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || active === panel)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [open]);

  function handleClick() {
    if (loading) return;
    if (ready) setOpen(o => !o);
    else navigate('/setup');
  }

  return (
    <>
      {/* The drawer stays MOUNTED whenever AI is ready (translated off-screen when
          closed, so it slides in on first open) so conversation state + the open-record
          ref survive open/close and the post-confirm continuation keeps its context.
          The one-time conversation-list fetch on mount is the accepted cost of that
          continuity (issue #14). */}
      {ready && (
        <>
          {open && (
            <div
              aria-hidden
              style={{
                position: 'fixed', inset: 0, zIndex: 58,
                background: SCRIM,
                animation: 'ck-fade-in 0.18s ease-out',
              }}
            />
          )}
          <div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-label="Assistant"
            aria-hidden={!open}
            tabIndex={-1}
            style={{
              // Constant z-index (not dropped on close) so the panel stays above the
              // page while it slides out; delayed visibility:hidden then removes it
              // from paint + hit-testing once closed.
              position: 'fixed', top: 0, bottom: 0, left: 0, zIndex: 59,
              width: 'min(420px, 100vw)',
              background: BG_CARD, borderRight: `1px solid ${LINE}`,
              boxShadow: `12px 0 32px ${SHADOW}`,
              display: 'flex', flexDirection: 'column', overflow: 'hidden',
              outline: 'none',
              transform: open ? 'translateX(0)' : 'translateX(-100%)',
              visibility: open ? 'visible' : 'hidden',
              // On close, delay `visibility:hidden` until the slide-out finishes so
              // the drawer actually animates off-screen (visibility isn't otherwise
              // transitionable); on open it flips to visible immediately.
              transition: open
                ? 'transform 0.22s ease-out'
                : 'transform 0.22s ease-out, visibility 0s linear 0.22s',
            }}
          >
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '12px 14px', borderBottom: `1px solid ${LINE}`,
            }}>
              <span style={{
                display: 'flex', alignItems: 'center', gap: 8,
                fontFamily: FONT_DISPLAY, fontSize: 16, color: INK,
              }}>
                <IconBot size={17} style={{ color: ACCENT_TEXT }} /> Assistant
              </span>
              <button
                onClick={() => { setOpen(false); btnRef.current?.focus(); }}
                aria-label="Close assistant"
                style={{
                  background: 'transparent', border: 'none', cursor: 'pointer',
                  color: INK_MUTE, display: 'flex', padding: 4,
                }}
              ><IconX size={16} /></button>
            </div>

            {/* ASSISTANT-PANEL-BODY: the chat surface (issue #4). Fills the drawer;
                recordContext makes it aware of the CRM record open behind it (#14). */}
            <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
              <AssistantPanelBody recordContext={record} />
            </div>
          </div>
        </>
      )}

      <button
        ref={btnRef}
        onClick={handleClick}
        aria-label="Assistant"
        aria-haspopup="dialog"
        // Admits this button into an `underLauncher` DetailModal's Tab cycle — rendering above
        // the panel makes it reachable by pointer, this makes it reachable by keyboard too.
        data-detail-companion=""
        aria-expanded={ready ? open : undefined}
        title={ready ? 'Assistant' : loading ? 'Assistant' : 'Hire your assistant'}
        disabled={loading}
        style={{
          // zIndex 40 — above a centred `DetailModal` under `underLauncher` (`dock:z-[39]`) so a
          // record detail can hand its context to the drawer (#14), and below that modal's
          // full-screen takeover (`z-50`), where a poked-through button would sit on the panel's
          // own controls. Keep the three in step.
          position: 'fixed', left: 24, bottom: 24, zIndex: 40,
          width: 52, height: 52, borderRadius: '50%',
          background: ACCENT, color: ACCENT_INK, border: 'none',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          boxShadow: `0 6px 18px ${SHADOW}`,
          cursor: loading ? 'default' : 'pointer',
          opacity: loading ? 0.55 : 1,
        }}
      >
        <IconBot size={24} strokeWidth={1.9} />
      </button>
    </>
  );
}
