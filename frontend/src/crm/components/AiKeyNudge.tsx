/**
 * AiKeyNudge — the dismissible first-run "add an AI key" prompt (issue #9).
 *
 * A prompt, never a gate: the CRM is fully usable with zero AI keys. Shown only
 * when no credentials are configured and the user hasn't dismissed it. Purely
 * presentational — durable dismissal lives in CrmLayout (POST
 * /api/crm/dismiss-ai-prompt). Mirrors DemoBanner's thin-banner shape, in the
 * accent-soft palette.
 */

import { Link } from 'react-router-dom';
import { IconSparkle, IconX } from '../../shared/icons';
import { INK, INK_MUTE, ACCENT, ACCENT_INK, ACCENT_SOFT, LINE, FONT_SANS } from '../../shared/styles';

export function AiKeyNudge({ onDismiss, isMobile }: {
  onDismiss: () => void; isMobile: boolean;
}) {
  return (
    <div style={{
      background: ACCENT_SOFT,
      borderBottom: `1px solid ${LINE}`,
      padding: isMobile ? '10px 16px' : '8px 28px',
      display: 'flex',
      flexDirection: isMobile ? 'column' : 'row',
      alignItems: isMobile ? 'flex-start' : 'center',
      justifyContent: 'space-between',
      gap: isMobile ? 8 : 16,
    }}>
      <span style={{
        display: 'flex', alignItems: 'center', gap: 8,
        fontFamily: FONT_SANS, fontSize: 13, color: INK, lineHeight: 1.4,
      }}>
        <IconSparkle size={15} style={{ color: ACCENT, flexShrink: 0 }} />
        <span>
          <strong style={{ fontWeight: 600 }}>Add an AI key to hire your assistant</strong>
          <span style={{ color: INK_MUTE }}> — smart import, drafting, and more.</span>
        </span>
      </span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
        <Link to="/setup" style={{
          background: ACCENT, color: ACCENT_INK,
          border: 'none', borderRadius: 4, padding: '5px 14px',
          fontSize: 12, fontFamily: FONT_SANS, fontWeight: 600,
          textDecoration: 'none', whiteSpace: 'nowrap',
        }}>Hire your assistant</Link>
        <button
          onClick={onDismiss}
          aria-label="Dismiss"
          title="Dismiss"
          style={{
            background: 'transparent', border: 'none', cursor: 'pointer',
            color: INK_MUTE, display: 'flex', alignItems: 'center', padding: 4,
          }}
        ><IconX size={15} /></button>
      </div>
    </div>
  );
}
