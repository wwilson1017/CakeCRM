/**
 * Board ⇄ List segmented control.
 *
 * Promotes the pill pattern that already shipped in
 * `the blueprint's view toggle` — the only segmented
 * control in the codebase — to a shared component, rather than inventing a
 * third toggle style. This is NOT covered by the design system's "underline
 * tabs only, no pill tabs" rule: that governs navigation tabs, whereas this
 * switches the rendering of one surface.
 *
 * Labels are "Board" and "List" rather than "Kanban": staff read the board as
 * "the board", and "List" is the issue's own word.
 */
import type { ViewMode } from './types';

const OPTIONS: { value: ViewMode; label: string }[] = [
  { value: 'kanban', label: 'Board' },
  { value: 'list', label: 'List' },
];

export default function ViewSwitcher<V extends string = ViewMode>({
  view,
  onChange,
  options,
}: {
  view: V;
  onChange: (v: V) => void;
  /** Override the Board/List pair — `shared/collection` adds a Cards segment. The default
   *  cast is sound only for consumers that omit this prop, whose `V` is `ViewMode`. */
  options?: readonly { value: V; label: string }[];
}) {
  // Through-unknown cast, sound only for consumers that omit `options` (V = ViewMode there).
  const opts = options ?? (OPTIONS as unknown as readonly { value: V; label: string }[]);
  return (
    <div role="group" aria-label="View" className="flex gap-1 rounded-lg bg-sand p-1">
      {opts.map(opt => (
        <button
          key={opt.value}
          type="button"
          // Only fire on an ACTUAL change. Consumers legitimately treat
          // `onChange` as "the view switched" and reset view-scoped state from
          // it — CRM clears its bulk selection — so a no-op click on the
          // already-active segment must not look like a switch. Guarding here
          // rather than in each consumer keeps every adopter safe by default.
          onClick={() => { if (opt.value !== view) onChange(opt.value); }}
          aria-pressed={view === opt.value}
          className={`px-3 py-1.5 rounded-md text-sm font-heading transition-colors ${
            view === opt.value
              ? 'bg-cream text-charcoal shadow-sm'
              : 'text-muted hover:text-charcoal'
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
