import { useCopyToClipboard } from '../../../shared/hooks/useCopyToClipboard';

interface Props {
  /** Built lazily — the sheet copies live form state, not the saved row. */
  text: () => string;
  /** What this copies, for screen readers ("Copy the whole todo"). */
  label: string;
}

/**
 * The Copy affordance used twice in the edit sheet: once beside the heading for
 * the whole todo, once beside the next-action label for just that line.
 *
 * Sized for a thumb (36px tall) rather than for the label it sits next to —
 * this is the one control on the sheet a phone user reaches for without meaning
 * to focus a field, so it must not be a mis-tap away from the input beside it.
 */
export function CopyButton({ text, label }: Props) {
  const { copied, copy } = useCopyToClipboard();

  return (
    <button
      type="button"
      onClick={() => void copy(text())}
      aria-label={label}
      title={label}
      className={`inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-heading transition-colors ${
        copied
          ? 'border-green-600 text-green-700 dark:text-green-300'
          : 'border-line text-muted hover:bg-sand'
      }`}
    >
      <span aria-hidden="true">{copied ? '✓' : '⧉'}</span>
      {copied ? 'Copied' : 'Copy'}
    </button>
  );
}
