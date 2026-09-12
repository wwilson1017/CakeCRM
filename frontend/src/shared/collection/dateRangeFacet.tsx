/**
 * `dateRangeFacet` (issue #181) — a from/to calendar-day filter for any surface, authored ONCE.
 *
 * It is a factory over the existing `custom` kind, not a sixth facet kind. `CustomFacetDef` is
 * documented as the escape hatch with a full value lifecycle, and a new kind would mean editing
 * the four switches in `facets.ts` plus the render switch in `CollectionView.tsx` plus the union
 * plus the barrel — five shared-layer edits whose only payoff is a chip renderer the `custom`
 * arm already lets a def supply. Every consumer supplies just `getDay`.
 *
 * A range facet is declared BESIDE its preset facet rather than merged into it: the layer ANDs
 * across active facets, so "presets plus a custom range" composes for free and the preset
 * predicates stay byte-identical.
 */
import ActiveChip from './ActiveChip';
import {
  EMPTY_DATE_RANGE,
  coerceDateRange,
  dateRangeActive,
  dateRangeChipLabel,
  dayInRange,
} from './dateRange';
import type { CustomFacetDef, DateRangeValue } from './types';

export interface DateRangeFacetOptions<T> {
  key: string;
  label: string;
  /** The item's viewer-local calendar day as `YYYY-MM-DD`, or `''` when it has none. */
  getDay: (item: T) => string;
}

const INPUT_CLASS =
  'rounded border border-line bg-cream px-2 py-1 text-xs text-charcoal';

export function dateRangeFacet<T>({
  key,
  label,
  getDay,
}: DateRangeFacetOptions<T>): CustomFacetDef<T, DateRangeValue> {
  return {
    kind: 'custom',
    key,
    label,
    defaultValue: EMPTY_DATE_RANGE,
    isActive: dateRangeActive,
    coerce: coerceDateRange,
    predicate: (item, value) => dayInRange(getDay(item), value),
    renderControl: (value, setValue) => (
      <div>
        <div className="mb-1.5 text-xs font-medium text-charcoal">{label}</div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="date"
            value={value.from ?? ''}
            aria-label={`${label} from`}
            onChange={e => setValue({ ...value, from: e.target.value || null })}
            className={INPUT_CLASS}
          />
          <span className="text-xs text-muted">to</span>
          <input
            type="date"
            value={value.to ?? ''}
            aria-label={`${label} to`}
            onChange={e => setValue({ ...value, to: e.target.value || null })}
            className={INPUT_CLASS}
          />
        </div>
      </div>
    ),
    renderChip: (value, clear) => (
      <ActiveChip label={dateRangeChipLabel(label, value)} onRemove={clear} />
    ),
  };
}
