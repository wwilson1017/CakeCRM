/**
 * AssistantLauncher — the persistent assistant affordance (issue #9).
 *
 * The assistant is a persistent affordance, not the home page: a fixed
 * bottom-right button always present in the CRM shell. It degrades gracefully
 * with zero AI keys — never an error:
 *   • aiReady === null   → unknown/loading: rendered but inert.
 *   • aiReady === false  → no key: routes to /setup ("hire your assistant").
 *   • aiReady === true   → opens a panel whose BODY is the mount slot for the
 *                          assistant chat surface (issue #4). #9 ships only the
 *                          launcher + panel shell + a placeholder body; #4 fills
 *                          the marked region on rebase after #9 lands.
 */

import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { IconBot, IconX } from '../../shared/icons';
import {
  INK, INK_MUTE, LINE, BG_CARD, ACCENT, ACCENT_INK, FONT_DISPLAY, FONT_SANS,
} from '../../shared/styles';

export function AssistantLauncher({ aiReady }: { aiReady: boolean | null }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

  const loading = aiReady === null;
  const ready = aiReady === true;

  function handleClick() {
    if (loading) return;
    if (ready) setOpen(o => !o);
    else navigate('/setup');
  }

  return (
    <>
      {ready && open && (
        <div
          role="dialog"
          aria-label="Assistant"
          style={{
            position: 'fixed', right: 24, bottom: 84, zIndex: 45,
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
              onClick={() => setOpen(false)}
              aria-label="Close assistant"
              style={{
                background: 'transparent', border: 'none', cursor: 'pointer',
                color: INK_MUTE, display: 'flex', padding: 4,
              }}
            ><IconX size={16} /></button>
          </div>

          {/* ASSISTANT-PANEL-BODY: issue #4 mounts the chat surface here.
              Until then, this placeholder confirms the assistant is available. */}
          <div style={{ padding: '18px 16px' }}>
            <p style={{
              fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE,
              lineHeight: 1.6, margin: 0,
            }}>
              Your assistant is ready. Chat is coming soon — meanwhile you can
              tune its model in <Link to="/setup" style={{ color: ACCENT }}>AI Setup</Link>.
            </p>
          </div>
        </div>
      )}

      <button
        onClick={handleClick}
        aria-label="Assistant"
        title={ready ? 'Assistant' : loading ? 'Assistant' : 'Hire your assistant'}
        disabled={loading}
        style={{
          position: 'fixed', right: 24, bottom: 24, zIndex: 40,
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
