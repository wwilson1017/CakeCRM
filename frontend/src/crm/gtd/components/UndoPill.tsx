// Todo-GTD — the undo block (#231; cake_os #2879 mark-done + #2925 inbox triage, design
// locked upstream to prototype C2).
//
// ONE framed object, not a pile of toasts. A persistent header that never moves carries
// the running count and (once there is more than one row) "Undo all"; each undoable
// action adds a FIXED-HEIGHT row below it. So the block grows and shrinks vertically
// only — the fixed width plus the truncated titles are what stop it resizing under the
// cursor, which was the specific objection to the stacked-toast treatment this replaces.
//
// Mounted once by `TodoShell`, fed by the module-level queue in `undoQueue.ts` — so it
// survives the shell's remount on every tab switch and a completion on Today is still
// undoable after the user moves to Inbox.
//
// WHERE it sits is this repo's answer, not the blueprint's. Upstream renders its toasts
// inside the todo shell and stacks the pill under them in one flex column; here toasts
// belong to the app-wide `ToastViewport` (bottom-right, 88px up to clear the "Ask Baker"
// launcher). The pill takes the same corner and the same 88px floor, and while it is
// mounted it publishes its own height as `--ck-toast-bottom` on the document root, which
// `ToastViewport` reads for its `bottom`. So the three fixed surfaces stack — launcher,
// then this block, then the toasts — and cannot overlap by construction. That matters
// because they co-occur BY DESIGN: filing an item fires the "Filed under …" toast at the
// very moment its undo row appears, and undoing a repeating todo fires the reopened-
// successor toast while the block is still showing.
import { useLayoutEffect, useRef } from 'react';
import { undoAll, undoOne } from '../hooks';
import { isCurrentSession, useUndoQueue, type UndoKind } from '../undoQueue';

/** The resting `bottom` of both the toast stack and this block — `ToastViewport`'s own
 *  number (24 + the 52px launcher + 12 clearance), which is `crm/styles.ts`'s
 *  `LAUNCHER_CLEARANCE_PX`; restated here rather than imported because that module
 *  drags the CRM's inline-style vocabulary into the GTD chunk for one integer. */
const STACK_BOTTOM_PX = 88;
/** Gap between this block's top edge and the first toast above it. */
const STACK_GAP_PX = 8;
/** The custom property `ToastViewport` reads. Set on <html> while the block is mounted,
 *  removed when it unmounts, so a toast raised with no block on screen lands at 88px. */
const TOAST_BOTTOM_VAR = '--ck-toast-bottom';

// The one place the two undoable actions are named. `row` labels the row; `header` is
// the same fact in the plural, for the count line. Copy, so it lives in the component
// and not in the store.
const KIND: Record<UndoKind, { row: string; header: string; verb: (title: string) => string }> = {
  done: {
    row: 'Marked done',
    header: 'marked done',
    verb: title => `Undo marking “${title}” done`,
  },
  filed: {
    row: 'Context set',
    header: 'filed from inbox',
    verb: title => `Undo filing “${title}”`,
  },
};

// Rows are h-11 (44px) rather than something tighter, and one value for both
// breakpoints keeps "fixed row height" literally true.
const ROW = 'flex h-11 items-center gap-2 px-3';
// The BUTTON is the tap target, not the row — the row has no click handler, so a compact
// button inside a 44px row leaves dead bands above and below it that swallow a thumb.
// `min-h-11` makes the button fill the row on mobile, where the design asks for large
// targets; from `sm` up it relaxes to the compact desktop size. Accent as TEXT routes
// through `text-ck-accent-text`, never `text-brand` (#119's FILL/TEXT split).
const ACTION =
  'shrink-0 inline-flex items-center justify-center min-h-11 sm:min-h-0 rounded-lg ' +
  'px-3 sm:px-2 py-1 text-sm font-heading font-semibold text-ck-accent-text ' +
  'hover:bg-sand focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand';

export function UndoPill() {
  // Only this session's rows: a seat swapped in by another tab must not see — or be
  // offered to revert — the previous seat's actions (see undoQueue.ts). Filtering at
  // render rather than clearing on the swap, because the swap reaches this tree through
  // no prop or context this component reads; the revert path re-checks regardless.
  const entries = useUndoQueue().filter(isCurrentSession);
  const box = useRef<HTMLDivElement>(null);

  // Publish the block's height to the toast stack. A layout effect keyed on the row
  // count — the block's height is a pure function of it (fixed header, fixed rows), so
  // there is nothing for a ResizeObserver to catch that this does not. Measured rather
  // than computed from the constants above, so a style change cannot silently desync
  // the two. The var is REMOVED, not zeroed, when the block leaves, so `ToastViewport`
  // falls back to its own 88px rather than a stale offset.
  useLayoutEffect(() => {
    const el = box.current;
    const style = document.documentElement.style;
    if (!el) {
      style.removeProperty(TOAST_BOTTOM_VAR);
      return;
    }
    style.setProperty(TOAST_BOTTOM_VAR, `${STACK_BOTTOM_PX + el.offsetHeight + STACK_GAP_PX}px`);
    return () => { style.removeProperty(TOAST_BOTTOM_VAR); };
  }, [entries.length]);

  if (entries.length === 0) return null;

  // The header names the action while every row is the same one; a queue holding both
  // kinds has no one action to name, and the per-row labels are what disambiguate it.
  const onlyKind = entries.every(e => e.kind === entries[0].kind) ? entries[0].kind : null;
  const heading = onlyKind
    ? `${entries.length} ${KIND[onlyKind].header}`
    : `${entries.length} recent changes`;

  return (
    <div
      ref={box}
      // `role="status"` + polite: the action is worth announcing, but never worth
      // interrupting what the screen reader is already saying.
      role="status"
      aria-live="polite"
      aria-label="Recent changes you can undo"
      // z-[100]: over the edit sheet (z-50) and the assistant drawer (59), like a toast,
      // and under `ConfirmHost` (150) and the toasts themselves (200). `bottom` is the
      // same 88px floor `ToastViewport` rests on, which is what keeps this off the
      // launcher; the toasts then move up by this block's height (see the effect above).
      className="fixed inset-x-3 bottom-[88px] z-[100] overflow-hidden rounded-xl border border-line bg-cream shadow-lg sm:inset-x-auto sm:right-5 sm:w-80"
    >
      <div className="flex h-11 items-center justify-between gap-2 border-b border-line-faint px-3">
        <span className="truncate font-heading text-sm font-semibold text-charcoal">
          {heading}
        </span>
        {/* Only once it stops being a second button for the same single row. */}
        {entries.length > 1 && (
          <button type="button" onClick={() => void undoAll()} className={ACTION}>
            Undo all
          </button>
        )}
      </div>
      <ul>
        {entries.map(entry => (
          <li key={entry.key} className={ROW}>
            {/* `min-w-0` is what lets `truncate` actually clip inside a flex row — without
                it the title sets the block's width and the fixed width is gone. */}
            <span className="min-w-0 flex-1 truncate text-sm text-muted" title={entry.title}>
              {entry.title}
            </span>
            {/* What happened to it. `shrink-0` so the title, not the label, is what gives
                way when the block runs out of room. */}
            <span className="shrink-0 text-xs text-muted">{KIND[entry.kind].row}</span>
            <button
              type="button"
              onClick={() => void undoOne(entry.key)}
              // "Undo?" alone names nothing when the rows are read out of context, and
              // the two kinds undo different things.
              aria-label={KIND[entry.kind].verb(entry.title)}
              className={ACTION}
            >
              Undo?
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
