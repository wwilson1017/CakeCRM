import type { SortDir, SortState } from './types';

interface Props {
  /**
   * Field options, in display order.
   *
   * Structurally a `{value,label}` list, but callers pass their `SortFieldDef[]` table
   * directly — which is the point: an option cannot then name a field that has no definition,
   * where the control would render a label and `sortItems` would silently return input order.
   * `readonly` is what lets an `as const satisfies` table be assigned here.
   */
  options: readonly { value: string; label: string }[];
  value: SortState;
  /** The selection to restore when the user clicks Reset. */
  defaultSort: SortState;
  onChange: (next: SortState) => void;
  /**
   * Whether the current selection counts as "active" — i.e. not the surface's resting order.
   *
   * Passed in rather than derived, and that is load-bearing twice over. A local
   * `value !== defaultSort` would be a reference comparison that is ALWAYS true, since every
   * `onChange({ ...value, dir })` mints a fresh object, so Reset and the active styling would
   * never turn off. And on a draggable board the page already computes this exact predicate
   * (`!isManualSort(...)`) to gate `dragDisabled` — taking it as a prop is what keeps the
   * control and the drag gate from drifting apart.
   */
  active: boolean;
  /** Optional trailing note, e.g. CRM's "· drag paused". Rendered only while `active`. */
  note?: string;
  /** Accessible name for the field select. */
  ariaLabel?: string;
}

/**
 * The shared sort control — a field dropdown plus an ascending/descending
 * toggle, generalised from CRM's `PipelineSortBar` with its visual language intact.
 *
 * A native `<select>` rather than a custom popover: the option list is short, and the native
 * control is the only one that is keyboard- and screen-reader-correct on the floor tablets
 * without re-implementing a roving-focus contract the design system explicitly warns against
 * half-building.
 */
export default function SortControl({
  options,
  value,
  defaultSort,
  onChange,
  active,
  note,
  ariaLabel = 'Sort by',
}: Props) {
  const dir: SortDir = value.dir;
  // A field the option list does not contain would render the native <select> with nothing
  // selected — a confusing blank control. Both consumers coerce persisted state against their
  // own field list so this is unreachable today, but `sortItems` and `isManualSort` both fail
  // safe on the same input and this control should not be the one place that doesn't.
  const shownField = options.some(o => o.value === value.field) ? value.field : defaultSort.field;

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-muted shrink-0">Sort</span>
      <select
        value={shownField}
        onChange={e => onChange({ ...value, field: e.target.value })}
        aria-label={ariaLabel}
        className={`px-2.5 py-1.5 rounded-lg text-sm border bg-cream transition-colors ${
          active ? 'border-brand text-ck-accent-text' : 'border-line text-charcoal'
        }`}
      >
        {options.map(o => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
      <button
        type="button"
        onClick={() => onChange({ ...value, dir: dir === 'asc' ? 'desc' : 'asc' })}
        title={dir === 'asc' ? 'Ascending — click for descending' : 'Descending — click for ascending'}
        aria-label={dir === 'asc' ? 'Sort ascending' : 'Sort descending'}
        className={`px-2.5 py-1.5 rounded-lg text-sm border transition-colors ${
          active
            ? 'bg-brand text-white border-brand'
            : 'bg-cream text-charcoal border-line hover:bg-sand'
        }`}
      >
        {dir === 'asc' ? '↑' : '↓'}
      </button>
      {active && (
        <button
          type="button"
          onClick={() => onChange(defaultSort)}
          className="text-xs text-muted hover:text-charcoal underline shrink-0"
        >
          Reset
        </button>
      )}
      {active && note && (
        <span className="text-xs text-muted shrink-0 hidden sm:inline">{note}</span>
      )}
    </div>
  );
}
