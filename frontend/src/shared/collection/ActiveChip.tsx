/**
 * Removable chip for a facet the collapsed row cannot render as a group selection
 * (the numeric `range`, and any `custom` facet that wants the standard look — `dateRangeFacet`
 * does). Its own module so two files can use it: a component declared during render remounts
 * its subtree every render, so it must stay at module scope.
 */
import { IconX } from '../icons';

export default function ActiveChip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-sand py-1 pl-2.5 pr-1 text-xs text-charcoal">
      {label}
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove filter ${label}`}
        className="inline-flex h-4 w-4 items-center justify-center rounded-full text-muted hover:bg-line/50 hover:text-charcoal"
      >
        <IconX className="h-3 w-3" aria-hidden="true" />
      </button>
    </span>
  );
}
