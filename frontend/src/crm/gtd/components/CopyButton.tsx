import { useCopyToClipboard } from '../../../shared/hooks/useCopyToClipboard';

interface Props {
  /** Built lazily — the sheet copies live form state, not the saved row. */
  text: () => string;
  /** What this copies, for screen readers ("Copy the whole todo"). */
  label: string;
}

const FACE = {
  idle: { glyph: '⧉', text: 'Copy', tone: 'border-line text-muted hover:bg-sand' },
  copied: { glyph: '✓', text: 'Copied', tone: 'border-green-600 text-green-700 dark:text-green-300' },
  failed: { glyph: '!', text: 'Copy failed', tone: 'border-line text-ck-accent-text' },
} as const;

/**
 * The Copy affordance used twice in the edit sheet: once beside the heading for
 * the whole todo, once beside the next-action label for just that line.
 *
 * Sized for a thumb (36px tall) rather than for the label it sits next to —
 * this is the one control on the sheet a phone user reaches for without meaning
 * to focus a field, so it must not be a mis-tap away from the input beside it.
 *
 * It reports failure rather than dropping it, which the blueprint does not do
 * and this port needs: the whole reason for the shared helper's legacy fallback
 * is the plain-http `/todo/{token}` install, so a browser where BOTH paths fail
 * is a real place this button lands. Silently staying on "Copy" there leaves the
 * user unable to tell a failed copy from a tap that never registered.
 *
 * Two accessibility mechanisms, deliberately, because they answer two different
 * questions. The `aria-label` leads with the VISIBLE word once there is an
 * outcome ("Copied — Copy the whole todo"): a fixed label would leave the
 * accessible name without the text on screen, which is WCAG 2.5.3 (Label in
 * Name) and, concretely, means a voice-control user saying "Copied" matches
 * nothing. The `role="status"` region is what actually ANNOUNCES the outcome —
 * screen readers vary in whether they re-read a changed name on the focused
 * element, and VoiceOver on the phone this targets is the least reliable of
 * them. Hearing it twice is the acceptable cost of not missing it.
 */
export function CopyButton({ text, label }: Props) {
  const { status, copy } = useCopyToClipboard();
  const face = FACE[status];

  return (
    <span className="inline-flex shrink-0 items-center">
      <button
        type="button"
        onClick={() => void copy(text())}
        aria-label={status === 'idle' ? label : `${face.text} — ${label}`}
        title={label}
        className={`inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-heading transition-colors ${face.tone}`}
      >
        <span aria-hidden="true">{face.glyph}</span>
        {face.text}
      </button>
      <span role="status" className="sr-only">
        {status === 'idle' ? '' : `${label}: ${face.text}`}
      </span>
    </span>
  );
}
