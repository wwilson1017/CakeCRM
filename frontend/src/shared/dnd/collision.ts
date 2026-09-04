/**
 * The board's collision strategy (issue #147, ported from the blueprint's shared Kanban).
 *
 * `closestCorners` — what `KanbanBoard` ran on from the #12 port until now — ranks droppables
 * by the distance between the corners of the DRAGGED rect and the corners of each droppable
 * rect. On a board whose columns are wildly different heights that is the wrong question. A
 * column's droppable is only as tall as its own cards, so when the source column is long and
 * the destination is short or empty, a card grabbed near the bottom of the long column stays
 * geometrically nearest to its own neighbours for the whole horizontal drag: the short column's
 * few cards sit hundreds of pixels higher up. The drop refuses to stick until the user also
 * drags UP into the band where the short column's content happens to live — the extra,
 * non-obvious motion the pipeline board demands whenever its stages are unevenly loaded, which
 * is the normal shape of a funnel: deals pile up in one stage while another sits empty. The
 * board's stage facet makes it sharper still, since narrowing to a few stages leaves the
 * survivors further apart in height.
 *
 * The thesis of the fix is that **the drop lands where you point**. Getting there takes four
 * pieces, applied to the rect map dnd-kit hands the strategy — never to the DOM, because a CSS
 * `min-h` big enough to matter would park a permanent blank slab, or a second scrollbar, under
 * every short column:
 *
 *   1. **Ask where the POINTER is, not where the dragged rect is** — `pointerWithin`, dnd-kit's
 *      answer for board layouts, and the one that matches what the user is aiming.
 *   2. **Grow every column to the board's full vertical extent** (`withFullHeightColumns`).
 *      Piece 1 is useless without this: a pointer at y=2000 over a short column whose rect ends
 *      at y=300 is inside NOTHING, so `pointerWithin` returns an empty list and the board has no
 *      target at all — strictly worse than before.
 *   3. **Close the gaps between cards** (`withClosedCardGaps`). The pipeline board's
 *      `columnClassName` is `flex flex-col gap-2 …`, so 8px of flex gap between consecutive card
 *      rects belongs to no droppable, and unlike `closestCorners` — which merely *ranked* by
 *      distance and so always named some card — `pointerWithin` demands literal containment.
 *      Land in that band on a cross-column drag and the only hit is the column, which
 *      `handleDragOver` reads as "append to the end"; since inserting at the end does not move
 *      the cards above the pointer, nothing reflows and the drop commits there. Deterministic,
 *      not transient. Closing the gaps means the pointer always names a card wherever a column's
 *      cards actually are, and names the column only in the free space below the last one —
 *      which is exactly where "append" is the right answer.
 *   4. **CARDS clipped to what the board actually shows** (`clampToBox`). Pieces 2 and 3
 *      both assume a rect is somewhere the board really is, and `getBoundingClientRect` ignores
 *      ancestor overflow, so anything past a fold reports a rect the user cannot see.
 *      Hit-testing those would let a drop released below the shortest lane commit to a card
 *      nobody can see. Two folds compose here, and CakeCRM ships both: a COLUMN that scrolls
 *      inside the board (the pipeline board's own `overflow-y-auto max-h-[70vh]`, which is the
 *      live vertical fold on every deal column) and the BOARD's own scroller box (`overflow-x-auto`
 *      on the container `KanbanBoard` marks with `data-kanban-scroller`, so the horizontal fold
 *      is the one that bites when more stages exist than fit the width). A card is cut to its
 *      column's visible part, which is itself cut to the scroller's, so one intersection covers
 *      both.
 *
 *      **The two axes are treated differently, and the split is the whole subtlety of this piece.**
 *      VERTICALLY, a lane is never dropped — only its cards are. The clip bounds the band piece 2
 *      draws and decides which cards are real, but a lane whose own cards have scrolled past the
 *      top of the board keeps its full-band rect and stays a drop target: the lane is still right
 *      there on screen beside the long one, and losing it would be a regression at the very moment
 *      you most want it — scroll deep into a 40-card lane and the 3-card lane next to it would stop
 *      accepting drops. HORIZONTALLY the opposite holds and the lane IS dropped (`clampX`), because
 *      a lane past the left or right fold is not beside anything: its x-range points at page space
 *      where none of the board is painted.
 *
 * **Cards beat their own column explicitly.** For two nested rects that both contain the point,
 * every corner of the outer one is at least as far away, so `pointerWithin`'s ranking already
 * favours the card — but only *strictly* nested rects. A column whose padding and gap happen to
 * add nothing around its only card has a rect identical to that card's, the two mean
 * corner-distances tie, and dnd-kit then falls back to droppable registration order, which puts
 * a column that mounted while empty ahead of a card added later. So the preference is applied
 * rather than inferred.
 *
 * Two behaviours worth knowing before changing any of it:
 *
 *   • Hovering the free space BELOW a column's last card during a SAME-column drag resolves to
 *     the column, and `KanbanBoard.handleDragOver` deliberately does nothing for that case. That
 *     is not a dead zone: the pointer cannot reach that space without first crossing the card
 *     above it, so the position that crossing established is simply held.
 *   • `closestCorners` remains the fallback for every case `pointerWithin` comes back empty on.
 *     Because the band follows the drag (see `withFullHeightColumns`), that is now essentially a
 *     HORIZONTAL question: the pointer in the gutter *between* two columns or off the end of the
 *     board, a layout that failed the widening precondition, and any pointer-less sensor —
 *     `pointerWithin` returns `[]` when `pointerCoordinates` is null, which is exactly what a
 *     KeyboardSensor produces. `sensors.ts` registers none today; this keeps adding one safe.
 */
import { closestCorners, pointerWithin } from '@dnd-kit/core';
import type { ClientRect, CollisionDetection, DroppableContainer, UniqueIdentifier } from '@dnd-kit/core';

type Measured = { id: UniqueIdentifier; rect: ClientRect };

/**
 * Cut a rect down to the part of it that an ancestor scroll box actually shows, on BOTH axes.
 *
 * `getBoundingClientRect` ignores ancestor overflow, so anything past a fold reports a rect
 * where the board is not. Two folds matter and they nest:
 *
 *   • **A column that scrolls internally.** The pipeline board's `columnClassName` carries
 *     `overflow-y-auto max-h-[70vh]`, which makes each column droppable its own scrollport: its
 *     rect stops at the fold while the cards below it keep reporting rects further down the page.
 *     This is the live vertical fold here, not a defensive one.
 *   • **The board's scroller.** The board container scrolls horizontally (`overflow-x-auto`), so
 *     a column past the right fold — and every card in it — reports real geometry off to the side
 *     of the visible board. A column's clip is intersected with the scroller's box before its
 *     cards are clipped to the column, so one call per card covers both folds. The clipped COLUMN
 *     rect is used for the band and for its cards; whether the column itself survives is decided
 *     separately, and only on the x axis, by `clampX` (see piece 4).
 *
 * **Both axes, not just y.** Clipping only the y-axis would leave a card scrolled past the
 * board's RIGHT (or left) fold pointer-hittable from the page gutter beside the board, which
 * `KanbanBoard`'s own contract ("hit-tested only where the board actually SHOWS them") rules out.
 * Intersecting on x as well as y is what makes that hold; a card's x is already inside its own
 * column's x, so clamping it there is a near-identity.
 *
 * Left alone they are pointer-hittable off-board: a pointer 100px under the shortest lane lands
 * "inside" an invisible card, `pointerWithin` therefore returns a hit, the `closestCorners`
 * fallback never runs, and the card preference makes that invisible card win — committing
 * `onMove` to a position the user never saw.
 *
 * Returns `null` for a rect with nothing left on EITHER axis, which drops that container from
 * consideration entirely — it must not be a target and must not take part in gap-closing either.
 * A card straddling a fold keeps its visible sliver, which is the right target. Returns the very
 * same object when it changed nothing, which is what the identity checks downstream rely on to
 * avoid cloning the rect map every frame.
 */
function clampToBox(rect: ClientRect, clip: ClientRect | null | undefined): ClientRect | null {
  if (!clip) return rect;
  const top = Math.max(rect.top, clip.top);
  const bottom = Math.min(rect.bottom, clip.bottom);
  const left = Math.max(rect.left, clip.left);
  const right = Math.min(rect.right, clip.right);
  if (bottom <= top || right <= left) return null;
  if (top === rect.top && bottom === rect.bottom && left === rect.left && right === rect.right) return rect;
  return { top, bottom, height: bottom - top, left, right, width: right - left };
}

/**
 * Cut a rect's HORIZONTAL range down to the part the board shows, leaving its y alone.
 *
 * This is the column's half of piece 4, and it is deliberately narrower than `clampToBox`. A lane
 * must survive its own cards scrolling out of view VERTICALLY — that is the rule piece 4 states,
 * and the moment you most want to drop into the short lane beside a long one. But a lane scrolled
 * past the board's LEFT or RIGHT fold is not beside anything: it is off the board entirely, and
 * its x-range points at page space where nothing of the board is painted. Left unclipped it is
 * pointer-hittable there — a drop released in the strip just outside the board's right edge can
 * land in a lane the user cannot see, which for the pipeline board means a deal silently changing
 * to a stage nobody chose.
 *
 * So x is clipped and y is not. `null` means nothing of the lane is horizontally on screen, and
 * the caller drops it from consideration entirely (rect map included — `pointerWithin` skips a
 * container with no rect, and a column left in the map would still be hit at its raw off-board
 * coordinates). Returns the same object when it changed nothing, for the identity checks
 * downstream.
 *
 * **This diverges from the blueprint, which clips only cards.** Its rationale for keeping every
 * lane covers the vertical case and does not reach this one; cards are already clipped on both
 * axes, so treating the x axis differently for lanes was the inconsistency, not the fix.
 *
 * The y axis has a mirror of the hole this closes, and it is inert HERE rather than absent: on the
 * widening's skip paths a lane vertically outside the board box keeps its raw y, since nothing
 * clamps it. No board in this repo can reach that, because none gives the SCROLLER a vertical fold
 * — `max-h-[70vh]` sits on the columns, whose droppable is itself the scrollport — so the board box
 * only ever bites on x. A consumer that bounds the board's own height would make it reachable, and
 * the fix would be to clamp y here too on exactly those paths.
 */
function clampX(rect: ClientRect, clip: ClientRect | null | undefined): ClientRect | null {
  if (!clip) return rect;
  const left = Math.max(rect.left, clip.left);
  const right = Math.min(rect.right, clip.right);
  if (right <= left) return null;
  if (left === rect.left && right === rect.right) return rect;
  return { top: rect.top, bottom: rect.bottom, height: rect.height, left, right, width: right - left };
}

/**
 * The attribute `KanbanBoard` marks its scroll region with, so this module can find it from a
 * droppable's own node. A machine contract, deliberately not a CSS class on the same element: a
 * class is free for anyone to restyle or rename, and the failure mode here is silent (cards
 * hit-testable in space the board does not occupy). `KanbanBoard.test.tsx` pins the pair.
 */
const SCROLLER_ATTRIBUTE = '[data-kanban-scroller]';

/**
 * The board's visible box — the scroller's rect, resolved by walking up from any measured
 * droppable, and `null` when nothing resolves (a board not yet mounted, or a test double with no
 * node, which is the unclipped baseline).
 *
 * Read here, at collision time, rather than handed in from the component: a closure over the
 * board's ref cannot leave render, and the rect must be current anyway, since it changes with
 * every window resize and with any scroll of the board.
 */
function boardVisibleBox(containers: DroppableContainer[]): ClientRect | null {
  for (const container of containers) {
    const node = container.node.current;
    const scroller = node?.closest(SCROLLER_ATTRIBUTE);
    if (scroller) return scroller.getBoundingClientRect();
  }
  return null;
}

/** Split the containers into the two kinds this board has, keeping only measured ones. */
function measured(rects: Map<UniqueIdentifier, ClientRect>, containers: DroppableContainer[]) {
  const viewportRect = boardVisibleBox(containers);
  const columns: Measured[] = [];
  /** Only the parts the board SHOWS bound the band — see `withFullHeightColumns`. */
  const bandRects: ClientRect[] = [];
  /** Per column: the part of it the board shows, or `null` for a column scrolled out of view. */
  const columnClipByKey = new Map<string, ClientRect | null>();
  const cardsByColumn = new Map<string, Measured[]>();
  /** Cards cut away by a fold, plus any lane with nothing horizontally on screen. */
  const hiddenIds: UniqueIdentifier[] = [];

  for (const container of containers) {
    const data = container.data.current;
    if (data?.type !== 'column') continue;
    const rect = rects.get(container.id);
    // Width, not area: a column is legitimately zero-HEIGHT on a board whose `columnClassName`
    // sets no `min-h` and whose consumer renders no empty placeholder, and it still needs
    // widening — that is half the point of this module. A zero-WIDTH rect is the `display: none`
    // all-zeros measurement, which cannot be pointed at, can never trip the x-range guard (its
    // `right` is 0), and would drag the band's top to viewport 0.
    if (!rect || rect.width <= 0) continue;
    // The lane keeps its full VERTICAL extent — its being a target must not depend on where its
    // own cards have scrolled to (piece 4) — but its x is clipped to the board, because a lane
    // past the horizontal fold is off the board rather than beside it. See `clampX`.
    const lane = clampX(rect, viewportRect);
    const visible = clampToBox(rect, viewportRect);
    if (!lane) {
      hiddenIds.push(container.id);
      // Its cards go with it: `null` here is what marks every one of them not-a-target below.
      columnClipByKey.set(String(data.columnId), null);
      continue;
    }
    columns.push({ id: container.id, rect: lane });
    if (visible) bandRects.push(visible);
    columnClipByKey.set(String(data.columnId), visible);
  }

  for (const container of containers) {
    const data = container.data.current;
    if (data?.type !== 'card') continue;
    const rect = rects.get(container.id);
    if (!rect) continue;
    const key = String(data.columnId);
    const clip = columnClipByKey.get(key);
    // `null` is a column with nothing on screen, so none of its cards are real targets. `undefined`
    // is a card whose column was never measured — a partial dnd-kit measurement state, not "no
    // column at all" — so it must still clip against the board's own box; falling through to the
    // card's full, unclipped rect would bypass the board clip entirely and leave it hit-testable
    // off-board for as long as that column stays unmeasured.
    const visible = clip === null ? null : clampToBox(rect, clip ?? viewportRect);
    if (!visible) {
      hiddenIds.push(container.id);
      continue;
    }
    const siblings = cardsByColumn.get(key);
    if (siblings) siblings.push({ id: container.id, rect: visible });
    else cardsByColumn.set(key, [{ id: container.id, rect: visible }]);
  }

  return { columns, bandRects, cardsByColumn, hiddenIds };
}

/**
 * Publish the clipped geometry — cards AND lanes — into the map the strategy actually hit-tests
 * against. A card cut down by a fold gets its visible sliver, and anything cut away entirely is
 * REMOVED: `pointerWithin` skips a container with no rect, which is precisely "not a target".
 *
 * `hiddenIds` carries the cards a fold erased plus any LANE with nothing horizontally on screen
 * (see `clampX`). A hidden lane must be deleted rather than merely skipped, or it stays hittable at
 * its raw, off-board coordinates.
 *
 * **Surviving lanes are written here rather than left to the widening**, which is not a tidiness
 * preference: `withFullHeightColumns` is the only other writer of a lane rect, and it has four
 * documented early returns (no columns, a non-finite band, a degenerate band, columns sharing an
 * x-range). On any of those the map would keep the lane's RAW x, and a pointer in the off-board
 * part of a straddling lane would target it — the horizontal clip silently switched off in exactly
 * the layouts that already failed a precondition. Publishing here makes the clip unconditional and
 * leaves the widening responsible for the vertical band alone.
 *
 * `clampToBox` and `clampX` hand back the very same object when they changed nothing, so the
 * identity checks below are what keep an unclipped board from cloning the map every frame for no
 * reason.
 */
function withVisibleGeometry(
  rects: Map<UniqueIdentifier, ClientRect>,
  columns: Measured[],
  cardsByColumn: Map<string, Measured[]>,
  hiddenIds: UniqueIdentifier[],
): Map<UniqueIdentifier, ClientRect> {
  let out = rects;
  const own = () => {
    if (out === rects) out = new Map(rects);
    return out;
  };

  for (const id of hiddenIds) own().delete(id);
  for (const { id, rect } of columns) {
    if (rects.get(id) !== rect) own().set(id, rect);
  }
  for (const cards of cardsByColumn.values()) {
    for (const { id, rect } of cards) {
      if (rects.get(id) !== rect) own().set(id, rect);
    }
  }
  return out;
}

/**
 * Widening a column vertically is only sound while the columns sit in ONE horizontal row,
 * because the pointer then tells them apart by x alone. Two columns sharing an x-range would
 * come out of the widening with IDENTICAL rects, and `pointerWithin` would hand back whichever
 * dnd-kit happened to register first — a fixed column, not the one under the pointer. That is
 * not hypothetical: a board whose columns wrap onto a second row, or one rendered without a
 * flex `className` at all, stacks them at the same x.
 *
 * So the row layout is a precondition, checked rather than assumed. O(n²) over a handful of
 * columns.
 *
 * The 1px tolerance keeps a fractional layout from reading as stacked. `getBoundingClientRect`
 * returns subpixel values, so two columns meant to sit flush could measure as overlapping by a
 * ten-thousandth of a pixel and silently switch the whole fix off — a failure with no symptom
 * except the original bug coming back on one machine. Nothing legitimate is lost: the boards
 * here space their columns with `gap-4`, and a genuinely stacked or wrapped layout overlaps by a
 * whole column width.
 */
const X_OVERLAP_TOLERANCE_PX = 1;

function columnsShareAnXRange(rects: ClientRect[]): boolean {
  for (let i = 0; i < rects.length; i++) {
    for (let j = i + 1; j < rects.length; j++) {
      const a = rects[i];
      const b = rects[j];
      if (a.left + X_OVERLAP_TOLERANCE_PX < b.right && b.left + X_OVERLAP_TOLERANCE_PX < a.right) {
        return true;
      }
    }
  }
  return false;
}

/**
 * Give every column a rect spanning the board's full vertical band — the union of the VISIBLE part
 * of each column **and the dragged card's own rect**. Horizontal extent is untouched:
 * which column the pointer is over is already correct, and leaving it alone is what keeps the
 * columns distinguishable.
 *
 * **Including the dragged rect is what stops the board oscillating.** The columns are re-measured
 * mid-drag — dnd-kit rebuilds every droppable rect when the container array changes identity,
 * which re-parenting the dragged card does — so the moment a card leaves the tallest column,
 * that column shrinks and the band shrinks with it. Drag the bottom card of the tallest column
 * straight across into a short one and the band can end up ABOVE the pointer that just put it
 * there: the widened rects no longer contain the pointer, the `closestCorners` fallback engages,
 * it picks the nearest remaining card back in the source column, and the card snaps home — where
 * it lengthens the column again, and the whole thing repeats every frame. Since the pointer is
 * always inside the dragged rect (it translates with the drag), unioning it in means the band
 * can never close above the pointer, and that loop cannot start.
 *
 * The side effect is deliberate and an improvement: dragging below or above the board keeps the
 * lane under the pointer as the target instead of falling back to a card in the source column.
 *
 * Skipped, leaving the rects as measured, whenever widening would be unsound or pointless: no
 * columns measured yet, columns sharing an x-range, or a band that did not come out finite.
 * `pointerWithin` still runs on the untouched rects in those cases — it just cannot reach into a
 * short column's empty space.
 */
function withFullHeightColumns(
  rects: Map<UniqueIdentifier, ClientRect>,
  columns: Measured[],
  bandRects: ClientRect[],
  draggedRect: ClientRect,
): Map<UniqueIdentifier, ClientRect> {
  let top = draggedRect.top;
  let bottom = draggedRect.bottom;
  // The columns' VISIBLE parts, not their raw rects: a lane running 2000px past the bottom of a
  // bounded board must not stretch the band down there with it, or a pointer below the board
  // would land in a lane instead of falling through to `closestCorners`. Every lane still
  // RECEIVES the band, including one with nothing on screen to contribute to it.
  for (const rect of bandRects) {
    top = Math.min(top, rect.top);
    bottom = Math.max(bottom, rect.bottom);
  }

  // `Number.isFinite` rather than a bare `bottom <= top`: a single NaN coordinate propagates
  // through Math.min/max, and every comparison against NaN is false, so `bottom <= top` would
  // wave it through and write NaN onto every column.
  // `=== 0`, not `< 2`. A ONE-column board is widened like any other, because the band starts from
  // the dragged rect and so is NOT that column's own rect: drag below the last card of the only
  // lane — a board filtered to one stage, or a transient frame with one column measured — and
  // without widening the pointer is inside nothing and falls back to a card further up.
  if (columns.length === 0 || !Number.isFinite(top) || !Number.isFinite(bottom) || bottom <= top) {
    return rects;
  }
  if (columnsShareAnXRange(columns.map(c => c.rect))) return rects;

  const widened = new Map(rects);
  for (const { id, rect } of columns) {
    // Built field by field rather than spread: the map's rects come from dnd-kit's own
    // measuring and need not be plain object literals.
    widened.set(id, {
      top,
      bottom,
      height: bottom - top,
      left: rect.left,
      right: rect.right,
      width: rect.width,
    });
  }
  return widened;
}

/**
 * Stretch each card UP to meet the card above it, so the flex `gap-2` band between them belongs
 * to somebody.
 *
 * **Up, not down** — the direction is the whole point, and getting it backwards is an
 * off-by-one. Targeting a card means "insert BEFORE this card": `handleDragOver` sets
 * `newIndex` to that card's index and `moveItem` splices there, pushing it down. So the gap
 * between cards i and i+1 has to resolve to card **i+1** for the drop to land between them;
 * giving it to card i would insert one slot too high, above card i.
 *
 * The first card keeps its top and the last keeps its bottom. That second one matters: the free
 * space under a column's last card stays the column's, which on a CROSS-column drag
 * `handleDragOver` reads as "append to the end" — exactly right, since there is no card there to
 * insert before. (On a same-column drag it does nothing at all; see the module docstring.)
 */
function withClosedCardGaps(
  rects: Map<UniqueIdentifier, ClientRect>,
  cardsByColumn: Map<string, Measured[]>,
): Map<UniqueIdentifier, ClientRect> {
  let closed = rects;

  for (const cards of cardsByColumn.values()) {
    if (cards.length < 2) continue;
    const ordered = [...cards].sort((a, b) => a.rect.top - b.rect.top);
    for (let i = 1; i < ordered.length; i++) {
      const { id, rect } = ordered[i];
      const previousBottom = ordered[i - 1].rect.bottom;
      // Only ever grow. Cards that already touch, or overlap because something transformed
      // them, are left exactly as measured.
      if (!(previousBottom < rect.top)) continue;
      if (closed === rects) closed = new Map(rects);
      closed.set(id, {
        top: previousBottom,
        bottom: rect.bottom,
        height: rect.bottom - previousBottom,
        left: rect.left,
        right: rect.right,
        width: rect.width,
      });
    }
  }
  return closed;
}

export const boardCollisionDetection: CollisionDetection = args => {
  const { columns, bandRects, cardsByColumn, hiddenIds } = measured(
    args.droppableRects,
    args.droppableContainers,
  );
  // The clipped map is kept separately because the FALLBACK needs it too: `closestCorners` merely
  // ranks by distance and so always names something, and handed the raw rects it will happily name
  // a card below a fold — undoing piece 4 in exactly the cases `pointerWithin` could not answer.
  // It gets the visible cards WITHOUT the widening, so it keeps ranking by real geometry rather
  // than by rects stretched to the whole board. Identical to `args.droppableRects` when nothing
  // was clipped, so an unfolded board's fallback is unchanged.
  const visibleRects = withVisibleGeometry(args.droppableRects, columns, cardsByColumn, hiddenIds);
  const droppableRects = withClosedCardGaps(
    withFullHeightColumns(visibleRects, columns, bandRects, args.collisionRect),
    cardsByColumn,
  );

  const pointerHits = pointerWithin({ ...args, droppableRects });
  if (pointerHits.length === 0) return closestCorners({ ...args, droppableRects: visibleRects });

  const cardIds = new Set<UniqueIdentifier>();
  for (const cards of cardsByColumn.values()) for (const { id } of cards) cardIds.add(id);
  const cardHits = pointerHits.filter(hit => cardIds.has(hit.id));
  return cardHits.length > 0 ? cardHits : pointerHits;
};
