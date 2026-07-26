import { INK_MUTE, FONT_MONO } from '../../shared/styles';
import { AI_BLUE, AI_BLUE_SOFT } from './badges';

// Small "AI" pill shown next to a field the assistant populated (issue #16), with an
// optional inline Confirm affordance. Rewritten from cake_os's Tailwind AiBadge to
// CakeCRM's inline-style idiom (matches badges.tsx). The AI_BLUE token (from badges.tsx)
// is a fixed informational blue — deliberately not ACCENT, which is user-rebrandable.

interface AiBadgeProps {
  source?: string | null;
  sourceDetail?: string | null;
  confidence?: number | null;
  onConfirm?: () => void;
  confirming?: boolean;
}

export function AiBadge({ source, sourceDetail, confidence, onConfirm, confirming }: AiBadgeProps) {
  const who = source === 'assistant' ? 'the assistant' : source || 'AI';
  const pct = confidence != null ? ` (confidence ${Math.round(confidence * 100)}%)` : '';
  const detail = sourceDetail ? ` Source: ${sourceDetail}.` : '';
  const tip = `AI-populated by ${who} — unverified${pct}.${detail}`;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, verticalAlign: 'middle' }}>
      <span
        title={tip}
        style={{
          fontFamily: FONT_MONO, fontSize: 10, letterSpacing: '0.08em', fontWeight: 600,
          padding: '1px 6px', borderRadius: 4, background: AI_BLUE_SOFT, color: AI_BLUE,
          textTransform: 'uppercase', whiteSpace: 'nowrap',
        }}
      >
        AI
      </span>
      {onConfirm && (
        <button
          onClick={onConfirm}
          disabled={confirming}
          title="Confirm this value is correct — clears the AI badge"
          style={{
            background: 'none', border: 'none', padding: 0, color: INK_MUTE,
            fontSize: 11, cursor: confirming ? 'default' : 'pointer', textDecoration: 'underline',
          }}
        >
          {confirming ? 'Confirming…' : 'Confirm'}
        </button>
      )}
    </span>
  );
}
