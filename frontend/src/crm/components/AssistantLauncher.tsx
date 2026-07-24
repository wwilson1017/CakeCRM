/**
 * AssistantLauncher — the persistent assistant affordance (issue #9).
 *
 * The assistant is a persistent affordance, not the home page: a fixed
 * bottom-LEFT button always present in the CRM shell (bottom-left keeps it clear
 * of the bottom-right toast column). It degrades gracefully with zero AI keys —
 * never an error:
 *   • aiReady === null   → unknown/loading: rendered but inert.
 *   • aiReady === false  → no key: routes to /setup ("hire your assistant").
 *   • aiReady === true   → opens a panel whose BODY is the assistant chat
 *                          surface (AssistantPanelBody, issue #4), mounted in
 *                          the region #9 reserved for it.
 */

import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AssistantPanelBody } from '../../assistant';
import { IconBot, IconX } from '../../shared/icons';
import {
  INK, INK_MUTE, LINE, BG_CARD, ACCENT, ACCENT_INK, FONT_DISPLAY,
} from '../../shared/styles';

export function AssistantLauncher({ aiReady }: { aiReady: boolean | null }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const loading = aiReady === null;
  const ready = aiReady === true;

  // Dialog dismissal: Escape (returns focus to the button) and outside-click.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') { setOpen(false); btnRef.current?.focus(); }
    }
    function onDown(e: MouseEvent) {
      const t = e.target as Node;
      if (panelRef.current?.contains(t) || btnRef.current?.contains(t)) return;
      setOpen(false);
    }
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onDown);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onDown);
    };
  }, [open]);

  function handleClick() {
    if (loading) return;
    if (ready) setOpen(o => !o);
    else navigate('/setup');
  }

  return (
    <>
      {ready && open && (
        <div
          ref={panelRef}
          role="dialog"
          aria-label="Assistant"
          style={{
            position: 'fixed', left: 24, bottom: 84, zIndex: 45,
            width: 320, maxWidth: 'calc(100vw - 48px)',
            background: BG_CARD, border: `1px solid ${LINE}`, borderRadius: 10,
            boxShadow: '0 12px 32px rgba(31,35,40,0.16)',
            display: 'flex', flexDirection: 'column', overflow: 'hidden',
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
              <IconBot size={17} style={{ color: ACCENT }} /> Assistant
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

          {/* ASSISTANT-PANEL-BODY: the chat surface (issue #4). The wrapper
              bounds the panel's height; the body fills it (height: 100%). */}
          <div style={{ height: 480, maxHeight: 'calc(100vh - 140px)', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            <AssistantPanelBody />
          </div>
        </div>
      )}

      <button
        ref={btnRef}
        onClick={handleClick}
        aria-label="Assistant"
        aria-haspopup="dialog"
        aria-expanded={ready ? open : undefined}
        title={ready ? 'Assistant' : loading ? 'Assistant' : 'Hire your assistant'}
        disabled={loading}
        style={{
          position: 'fixed', left: 24, bottom: 24, zIndex: 40,
          width: 52, height: 52, borderRadius: '50%',
          background: ACCENT, color: ACCENT_INK, border: 'none',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          boxShadow: '0 6px 18px rgba(31,35,40,0.22)',
          cursor: loading ? 'default' : 'pointer',
          opacity: loading ? 0.55 : 1,
        }}
      >
        <IconBot size={24} strokeWidth={1.9} />
      </button>
    </>
  );
}
