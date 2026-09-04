import { describe, it, expect } from 'vitest';
import { closestCorners } from '@dnd-kit/core';
import type { Active, ClientRect, DroppableContainer, UniqueIdentifier } from '@dnd-kit/core';
import { boardCollisionDetection } from './collision';

/**
 * Issue #147 — dragging out of a long column into a short/empty one used to require an extra
 * upward motion, because `closestCorners` ranks by distance to the DRAGGED rect and the short
 * column's droppable is only as tall as its own (few) cards.
 *
 * The board modelled below is the exact repro shape: column A is 20 cards deep, column B holds
 * nothing (or one card at the top), and the user has dragged a card from the bottom of A across
 * to B without moving up. Every rect is viewport space, which is what dnd-kit measures in.
 *
 * The first case asserts the OLD strategy's answer alongside the new one — without that the test
 * would still pass if the widening were deleted and `pointerWithin` happened to agree.
 */

/**
 * Put the containers inside a board whose visible box is `box` — the strategy resolves it by
 * walking up from a droppable's own node, so a double needs only `closest`. Every case that is
 * not about the board's own fold skips this, which models a board whose scroller has not been
 * measured yet: the unclipped baseline, where only the column-level clip applies.
 */
function inBoardBox<T extends { droppableContainers: DroppableContainer[] }>(input: T, box: ClientRect): T {
  const scroller = { getBoundingClientRect: () => box } as unknown as Element;
  return {
    ...input,
    droppableContainers: input.droppableContainers.map(c => ({
      ...c,
      node: { current: { closest: () => scroller } as unknown as HTMLElement },
    })),
  };
}

const COLUMN_TOP = 100;
const A_LEFT = 0;
const B_LEFT = 320;
const COLUMN_WIDTH = 300;
const A_BOTTOM = 2100; // 20 cards deep — the board's full vertical extent

function rect(left: number, top: number, width: number, height: number): ClientRect {
  return { left, top, width, height, right: left + width, bottom: top + height };
}

function column(id: string): DroppableContainer {
  return droppable(`column-${id}`, { type: 'column', columnId: id });
}

function droppable(id: string, data: Record<string, unknown>): DroppableContainer {
  return {
    id,
    key: id,
    data: { current: data },
    disabled: false,
    node: { current: null },
    rect: { current: null },
  };
}

function card(id: string, columnId: string): DroppableContainer {
  return droppable(`card-${id}`, { type: 'card', itemId: id, columnId });
}

/** The dragged card, mid-flight over column B near the bottom of the board. */
const DRAGGED_RECT = rect(B_LEFT, 1950, 280, 80);

const active: Active = {
  id: 'card-a20',
  data: { current: { type: 'card', itemId: 'a20', columnId: 'A' } },
  rect: { current: { initial: DRAGGED_RECT, translated: DRAGGED_RECT } },
};

/**
 * A board with a 20-card column A and a column B holding `bCards` cards at the top.
 * Card rects are only laid out where a test needs them: the bottom of A (what `closestCorners`
 * wrongly latches onto) and the top of B.
 */
function board(bCards: number) {
  const containers: DroppableContainer[] = [column('A'), column('B')];
  // B's droppable wraps its own cards (10px in, 60px tall, 10px apart) — or, when it has none,
  // just the empty placeholder. Either way it sits at the TOP of the board, which is the whole
  // problem: a card dragged from the bottom of A is nowhere near it.
  const bHeight = bCards === 0 ? 40 : 10 + (bCards - 1) * 70 + 60;
  const rects = new Map<UniqueIdentifier, ClientRect>([
    ['column-A', rect(A_LEFT, COLUMN_TOP, COLUMN_WIDTH, A_BOTTOM - COLUMN_TOP)],
    ['column-B', rect(B_LEFT, COLUMN_TOP, COLUMN_WIDTH, bHeight)],
  ]);

  for (let i = 0; i < 20; i++) {
    const id = `a${i + 1}`;
    containers.push(card(id, 'A'));
    rects.set(`card-${id}`, rect(A_LEFT + 10, COLUMN_TOP + i * 100, 280, 80));
  }
  for (let i = 0; i < bCards; i++) {
    const id = `b${i + 1}`;
    containers.push(card(id, 'B'));
    rects.set(`card-${id}`, rect(B_LEFT + 10, COLUMN_TOP + 10 + i * 70, 280, 60));
  }

  return { droppableContainers: containers, droppableRects: rects };
}

function args(pointerCoordinates: { x: number; y: number } | null, bCards = 0) {
  return { active, collisionRect: DRAGGED_RECT, pointerCoordinates, ...board(bCards) };
}

describe('boardCollisionDetection', () => {
  it('targets an empty column the pointer is over, however far down the drag started', () => {
    const input = args({ x: 470, y: 2000 });

    // The bug, pinned: nearest-corner distance still prefers a neighbour in the source column.
    expect(String(closestCorners(input)[0]?.id)).toMatch(/^card-a\d+$/);

    expect(boardCollisionDetection(input)[0]?.id).toBe('column-B');
  });

  it('prefers a card under the pointer over its own column, so per-index insertion survives', () => {
    // Independent of the widening — a card is always inside its column's NATIVE rect too, so
    // there is no arrangement where this could depend on it. What it pins is that widening the
    // column did not overtake the card: `pointerWithin` ranks by mean distance to a rect's four
    // corners, and the enclosing rect's corners are never nearer.
    expect(boardCollisionDetection(args({ x: 470, y: 140 }, 1))[0]?.id).toBe('card-b1');
  });

  it('does not widen when two columns share an x-range, which would make their rects identical', () => {
    // A board rendered with no flex `className`: fixed-width column wrappers stack as block
    // boxes, same x, different y. Widening would give both columns the same rect and hand every
    // drop to whichever dnd-kit registered first, regardless of the pointer.
    const stacked = {
      active,
      collisionRect: DRAGGED_RECT,
      pointerCoordinates: { x: 150, y: 400 },
      droppableContainers: [column('A'), column('B')],
      droppableRects: new Map<UniqueIdentifier, ClientRect>([
        ['column-A', rect(0, 100, 300, 200)], // y 100..300
        ['column-B', rect(0, 320, 300, 200)], // y 320..520 — directly below A, same x
      ]),
    };
    // Unwidened, the pointer at y=400 is inside B's own rect and nothing else, so the strategy
    // still resolves correctly here — it just does it without the widening.
    expect(boardCollisionDetection(stacked)[0]?.id).toBe('column-B');
  });

  it('treats a sub-pixel column overlap as adjacent, not stacked, and still widens', () => {
    // Fractional layout: A ends at 300.5 and B starts at 300.0 — a half-pixel overlap that a
    // strict comparison would read as "stacked", switching the whole fix off with no symptom
    // beyond the original bug reappearing. The pointer sits deep below B's own rect, so it
    // resolves to column-B only if the widening actually ran.
    const fractional = {
      active,
      collisionRect: DRAGGED_RECT,
      pointerCoordinates: { x: 450, y: 1500 },
      droppableContainers: [column('A'), column('B')],
      droppableRects: new Map<UniqueIdentifier, ClientRect>([
        ['column-A', rect(0, COLUMN_TOP, 300.5, A_BOTTOM - COLUMN_TOP)], // right = 300.5
        ['column-B', rect(300, COLUMN_TOP, 300, 40)], // left = 300.0 — overlaps A by 0.5px
      ]),
    };
    expect(boardCollisionDetection(fractional)[0]?.id).toBe('column-B');
  });

  it('resolves a single-column board, which is never widened (the union is its own rect)', () => {
    const single = {
      active,
      collisionRect: DRAGGED_RECT,
      pointerCoordinates: { x: 150, y: 290 },
      droppableContainers: [column('A')],
      droppableRects: new Map<UniqueIdentifier, ClientRect>([['column-A', rect(0, 100, 300, 200)]]),
    };
    expect(boardCollisionDetection(single)[0]?.id).toBe('column-A');
  });

  it('names the card BELOW the gap between two cards, so the drop lands between them', () => {
    // Cards in column B sit at y 110..170 and 180..240 — a 10px band at y 170..180 (the fixture's
    // round stand-in for the board's real 8px `gap-2`) belongs to neither rect as measured. `pointerWithin` needs literal containment, so without
    // gap-closing the only hit is the column, which handleDragOver reads as "append to the end"
    // — and since appending never moves the cards above the pointer, nothing reflows it away.
    //
    // It must resolve to card-b2, not card-b1: targeting a card inserts BEFORE it, so b2 is what
    // puts the drop between the two. b1 would land it one slot too high, above b1.
    expect(boardCollisionDetection(args({ x: 470, y: 175 }, 2))[0]?.id).toBe('card-b2');
  });

  it('still names the column in the free space below a column last card', () => {
    // Below card-b2 (ends at y=240) there is no card to insert before, and appending is right.
    expect(boardCollisionDetection(args({ x: 470, y: 600 }, 2))[0]?.id).toBe('column-B');
  });

  it('prefers a card over a column whose rect is identical to it', () => {
    // A column whose padding and flex gap happen to add nothing around its only card has the
    // same rect as that card. The corner distances then TIE, and dnd-kit breaks ties by
    // registration order — which puts a column that mounted while empty ahead of a card added
    // later. Registration order is reproduced here by listing the column first, which is what
    // makes this fail without the explicit card preference.
    const identical = {
      active,
      collisionRect: DRAGGED_RECT,
      pointerCoordinates: { x: 150, y: 150 },
      droppableContainers: [column('A'), card('a1', 'A')],
      droppableRects: new Map<UniqueIdentifier, ClientRect>([
        ['column-A', rect(0, 100, 300, 100)],
        ['card-a1', rect(0, 100, 300, 100)], // byte-for-byte the column's rect
      ]),
    };
    expect(boardCollisionDetection(identical)[0]?.id).toBe('card-a1');
  });

  it('holds the target after the source column shrinks beneath the pointer', () => {
    // The second frame of dragging the BOTTOM card of the tallest column straight across into a
    // short one. Frame 1 put the card in B; dnd-kit then re-measured every droppable (it rebuilds
    // them whenever the container array changes identity, which re-parenting the dragged card
    // does), so A is now one card shorter and the band drawn from the columns alone ends ABOVE
    // the pointer that just did the drag.
    //
    // Bounding the band by the columns only, the widened rects would no longer contain the
    // pointer, closestCorners would engage, pick card-a19 back in column A, and snap the card
    // home — where it lengthens A again and the next frame repeats. Unioning the dragged rect
    // into the band is what makes this frame stable.
    const containers: DroppableContainer[] = [column('A'), column('B')];
    const rects = new Map<UniqueIdentifier, ClientRect>([
      ['column-A', rect(A_LEFT, COLUMN_TOP, COLUMN_WIDTH, 1892)], // 100..1992 — a20 has left
      ['column-B', rect(B_LEFT, COLUMN_TOP, COLUMN_WIDTH, 92)], // 100..192 — a20 landed here
    ]);
    for (let i = 0; i < 19; i++) {
      const id = `a${i + 1}`;
      containers.push(card(id, 'A'));
      rects.set(`card-${id}`, rect(A_LEFT + 10, COLUMN_TOP + i * 100, 280, 92));
    }
    containers.push(card('a20', 'B'));
    rects.set('card-a20', rect(B_LEFT + 10, COLUMN_TOP, 280, 92));

    const dragged = rect(B_LEFT, 2000, 280, 92); // still down where the user grabbed it
    const frame2 = {
      active,
      collisionRect: dragged,
      pointerCoordinates: { x: 470, y: 2040 }, // inside the dragged card, below A's new bottom
      droppableContainers: containers,
      droppableRects: rects,
    };
    // column-B, so handleDragOver's same-column branch holds the card where frame 1 put it.
    expect(boardCollisionDetection(frame2)[0]?.id).toBe('column-B');
  });

  it('ignores cards clipped out of view by a column that scrolls internally', () => {
    // The pipeline board's `columnClassName` carries `overflow-y-auto max-h-[70vh]`, which makes
    // each column droppable its own scrollport, so its rect stops at the fold. But
    // `getBoundingClientRect` ignores ancestor overflow, so the cards past the fold still report
    // rects further down the page (the board-level fold below has the same shape). Unclamped
    // they are hittable in space the board does not occupy, `pointerWithin` returns a hit so the
    // closestCorners fallback never runs, and the card preference hands the drop to a card the
    // user cannot see.
    const containers: DroppableContainer[] = [column('A'), column('B')];
    const rects = new Map<UniqueIdentifier, ClientRect>([
      ['column-A', rect(A_LEFT, COLUMN_TOP, COLUMN_WIDTH, 700)], // scrollport: y 100..800
      ['column-B', rect(B_LEFT, COLUMN_TOP, COLUMN_WIDTH, 40)],
    ]);
    for (let i = 0; i < 20; i++) {
      const id = `a${i + 1}`;
      containers.push(card(id, 'A'));
      rects.set(`card-${id}`, rect(A_LEFT + 10, COLUMN_TOP + i * 100, 280, 92)); // runs to y=2092
    }
    const below = {
      active,
      collisionRect: DRAGGED_RECT,
      pointerCoordinates: { x: 150, y: 900 }, // below column A's fold, still in its lane
      droppableContainers: containers,
      droppableRects: rects,
    };
    // card-a9 lives at y 900..992 — real geometry, invisible on screen. Unclamped it would win
    // outright, because the card preference puts any card ahead of its column.
    const hits = boardCollisionDetection(below).map(hit => String(hit.id));
    expect(hits).toContain('column-A'); // the lane under the pointer, which is the right target
    expect(hits.filter(id => id.startsWith('card-'))).toEqual([]);
  });

  it('ignores a display:none column instead of dragging the board band up to y=0', () => {
    // An all-zeros measurement can never trip the x-range guard (its `right` is 0) and would
    // pull the union's top to the viewport origin, widening every column up under the header.
    // The test asserts the SURVIVING columns still behave: the pointer is above the real board
    // top, so it must fall through rather than land in a band stretched up to meet it.
    const withHidden = {
      active,
      collisionRect: DRAGGED_RECT,
      pointerCoordinates: { x: 470, y: 40 },
      droppableContainers: [column('A'), column('B'), column('HIDDEN')],
      droppableRects: new Map<UniqueIdentifier, ClientRect>([
        ['column-A', rect(A_LEFT, COLUMN_TOP, COLUMN_WIDTH, A_BOTTOM - COLUMN_TOP)],
        ['column-B', rect(B_LEFT, COLUMN_TOP, COLUMN_WIDTH, 40)],
        ['column-HIDDEN', rect(0, 0, 0, 0)],
      ]),
    };
    expect(boardCollisionDetection(withHidden)).toEqual(closestCorners(withHidden));
  });

  it("clips a lane to the board's own scroll box, so a card past the fold is not a target", () => {
    // The second fold: the board container itself, which `KanbanBoard` marks with
    // `data-kanban-scroller`. Column A's rect runs to y=2100, but the board shows only
    // y 100..900, so cards below 900 are real geometry the user cannot see.
    const input = args({ x: 150, y: 1500 });

    // Unclipped, the invisible card wins outright — the card preference puts any card ahead of
    // its column. That is the bug this clip exists to stop, asserted rather than assumed.
    expect(boardCollisionDetection(input)[0]?.id).toBe('card-a15');

    const hits = boardCollisionDetection(inBoardBox(input, rect(A_LEFT, 100, 640, 800)))
      .map(hit => String(hit.id));
    expect(hits.filter(id => id.startsWith('card-'))).toEqual([]);
    expect(hits).toContain('column-A'); // the lane under the pointer, which is the right target
  });

  it('keeps the visible sliver of a card straddling the board fold', () => {
    // Cut down, not dropped: the part of card a9 above the fold is exactly the target a pointer
    // there is aiming at. a9 spans y 900..980 and the board ends at y=950.
    const box = rect(A_LEFT, 100, 640, 850);
    expect(boardCollisionDetection(inBoardBox(args({ x: 150, y: 920 }), box))[0]?.id).toBe('card-a9');
    // 10px below the fold, inside a9's real rect but off-board: the lane, not the card.
    expect(boardCollisionDetection(inBoardBox(args({ x: 150, y: 960 }), box)).map(h => String(h.id)))
      .not.toContain('card-a9');
  });

  it("removes a card scrolled past the board's horizontal fold, while its column stays a target and a straddling neighbor keeps its sliver", () => {
    // Three columns side by side, wider than the board actually shows: A is fully on screen, B
    // straddles the right edge, C sits entirely past it — the shape of the pipeline board, whose
    // container is `overflow-x-auto` and routinely holds more stages than fit the width. Only
    // the CARDS should be affected; a scrolled-away column stays a drop target either way
    // (piece 4's existing rule, unchanged by this fix).
    const zDragged = rect(10, 110, 280, 80);
    const zActive: Active = {
      id: 'card-dragging',
      data: { current: { type: 'card', itemId: 'dragging', columnId: 'A' } },
      rect: { current: { initial: zDragged, translated: zDragged } },
    };
    const containers: DroppableContainer[] = [
      column('A'), column('B'), column('C'),
      card('a1', 'A'), card('b1', 'B'), card('c1', 'C'),
    ];
    const rects = new Map<UniqueIdentifier, ClientRect>([
      ['column-A', rect(0, 100, 300, 200)],
      ['column-B', rect(500, 100, 300, 200)], // x 500..800 — half inside the board's x 0..620
      ['column-C', rect(900, 100, 300, 200)], // x 900..1200 — entirely past the fold
      ['card-a1', rect(10, 110, 280, 80)],
      ['card-b1', rect(510, 110, 280, 80)], // x 510..790 — straddles the fold at 620
      ['card-c1', rect(910, 110, 280, 80)], // x 910..1190 — entirely off-board
    ]);
    const base = { active: zActive, collisionRect: zDragged, droppableContainers: containers, droppableRects: rects };
    const box = rect(0, 100, 620, 800); // the board shows x 0..620, y 100..900

    // c1's real rect is fully right of the board box, so no pointer position can reach it — the
    // pointer here sits exactly where c1 really is, and only its (widened) column answers.
    const farHits = boardCollisionDetection(inBoardBox({ ...base, pointerCoordinates: { x: 950, y: 150 } }, box))
      .map(hit => String(hit.id));
    expect(farHits).not.toContain('card-c1');
    expect(farHits).toContain('column-C');

    // b1 straddles the fold; its visible sliver (x 510..620) is still real, so a pointer there
    // still names the card, not just the column.
    expect(
      boardCollisionDetection(inBoardBox({ ...base, pointerCoordinates: { x: 600, y: 150 } }, box))[0]?.id,
    ).toBe('card-b1');
  });

  it('clips a card whose column was never measured to the board box, instead of keeping its full rect', () => {
    // Card z1 belongs to column 'Z', which has NO droppable container of its own — a real dnd-kit
    // state (a column not yet mounted, or one whose own droppable has not registered). That makes
    // `columnClipByKey.get('Z')` come back `undefined`, not `null`, and the fix must not read that
    // as "no clip at all".
    const zDragged = rect(400, 900, 280, 100); // the card currently being dragged, near the fold
    const zActive: Active = {
      id: 'card-dragging',
      data: { current: { type: 'card', itemId: 'dragging', columnId: 'A' } },
      rect: { current: { initial: zDragged, translated: zDragged } },
    };
    const containers: DroppableContainer[] = [column('A'), column('B'), card('z1', 'Z')];
    const rects = new Map<UniqueIdentifier, ClientRect>([
      ['column-A', rect(0, 100, 300, 800)], // on-screen, y 100..900
      ['column-B', rect(400, 100, 300, 200)], // a second column, so the board widens
      ['card-z1', rect(10, 900, 280, 80)], // its own column was never measured — y 900..980
    ]);
    const base = { active: zActive, collisionRect: zDragged, droppableContainers: containers, droppableRects: rects };

    // Unclipped baseline (no board box resolved yet): real geometry, so a pointer inside z1's raw
    // rect hits it.
    expect(boardCollisionDetection({ ...base, pointerCoordinates: { x: 150, y: 960 } })[0]?.id).toBe('card-z1');

    // The board shows only y 100..950. z1's column was never measured, but it must still clip
    // against the board's own box: the pointer, past the clipped bottom, no longer reaches the
    // card — it falls through to the widened column instead (the dragged rect keeps the band open
    // past 950, the same mechanism as any other lane).
    const box = rect(0, 100, 620, 850);
    const hits = boardCollisionDetection(inBoardBox({ ...base, pointerCoordinates: { x: 150, y: 960 } }, box))
      .map(hit => String(hit.id));
    expect(hits).not.toContain('card-z1');
    expect(hits).toContain('column-A');

    // Its visible sliver (y 900..950) is still real, so a pointer inside that keeps hitting it.
    expect(
      boardCollisionDetection(inBoardBox({ ...base, pointerCoordinates: { x: 150, y: 920 } }, box))[0]?.id,
    ).toBe('card-z1');
  });

  it('keeps a lane whose own cards have scrolled out of the board as a drop target', () => {
    // The board shows y 1000..1800 — the user is deep inside 40-card column A, so column B's two
    // cards (up at y ~100) are far above the top of the board. Before the board was bounded this
    // lane accepted a drop at any height, and it still must: this is exactly the moment someone
    // wants to move a card into the short lane beside the long one. What must NOT survive is B's
    // invisible CARDS — a drop there would commit to a position nobody can see.
    const input = inBoardBox(args({ x: 470, y: 1200 }, 2), rect(A_LEFT, 1000, 640, 800));
    const hits = boardCollisionDetection(input).map(hit => String(hit.id));
    expect(hits).toContain('column-B');
    expect(hits.filter(id => id.startsWith('card-'))).toEqual([]);
  });

  it('still targets the lane under the pointer when the drag leaves the board vertically', () => {
    // Not a test of the clip — a guard that the clip did not break the dragged-rect union. The
    // rect is unioned into the band as always, so dragging below the board keeps the lane under
    // the pointer instead of snapping back to a card in the source column: the clip bounds the
    // COLUMNS, deliberately not the drag.
    const input = inBoardBox(args({ x: 470, y: 2000 }), rect(A_LEFT, 100, 640, 800));
    expect(boardCollisionDetection(input)[0]?.id).toBe('column-B');
  });

  it('returns nothing on a board with no measured droppables, rather than throwing', () => {
    const empty = {
      active,
      collisionRect: DRAGGED_RECT,
      pointerCoordinates: { x: 150, y: 290 },
      droppableContainers: [],
      droppableRects: new Map<UniqueIdentifier, ClientRect>(),
    };
    expect(boardCollisionDetection(empty)).toEqual([]);
  });

  it('does not widen columns horizontally — the gutter between them is nobody', () => {
    // x=310 is in the 20px gap between A (ends at 300) and B (starts at 320).
    const input = args({ x: 310, y: 2000 });
    expect(boardCollisionDetection(input)).toEqual(closestCorners(input));
  });

  it('falls back to closestCorners above the board, where no column is under the pointer', () => {
    const input = args({ x: 470, y: 50 }); // above COLUMN_TOP — the toolbar band
    expect(boardCollisionDetection(input)).toEqual(closestCorners(input));
  });

  it('falls back to closestCorners for a pointer-less sensor', () => {
    const input = args(null);
    const collisions = boardCollisionDetection(input);
    expect(collisions).toEqual(closestCorners(input));
    expect(collisions.length).toBeGreaterThan(0);
  });

  it('skips widening entirely when a column measures NaN, rather than writing NaN onto every column', () => {
    // The reason `withFullHeightColumns` tests `Number.isFinite` instead of a bare `bottom <= top`.
    // A single NaN coordinate propagates through Math.min/max into the band, and every comparison
    // against NaN is false — so `bottom <= top` waves it through and stamps a NaN-bounded rect onto
    // EVERY column, including the ones that measured perfectly well. `pointerWithin` then contains
    // nothing at all, the closestCorners fallback engages, and it answers with a card back in the
    // source column.
    //
    // Only `top` is poisoned, which is what a partial measurement actually looks like.
    const { droppableContainers, droppableRects } = board(0);
    droppableRects.set('column-A', { ...droppableRects.get('column-A')!, top: NaN });

    const poisoned = {
      active,
      collisionRect: DRAGGED_RECT,
      // Inside column B's own small, correctly-measured rect (y 100..140) — so the answer does not
      // depend on the widening, only on the widening having been SKIPPED rather than botched.
      pointerCoordinates: { x: 470, y: 120 },
      droppableContainers,
      droppableRects,
    };
    expect(boardCollisionDetection(poisoned)[0]?.id).toBe('column-B');
  });

  it('leaves already-overlapping card rects exactly as measured instead of shrinking one', () => {
    // `withClosedCardGaps` only ever GROWS a card upward. Two cards whose rects already overlap —
    // because a transform moved them, or a drag animation is mid-flight — must be left alone: the
    // grow logic run unconditionally would drag b2's top down to b1's bottom, cutting away the
    // overlap band that is real, visible hit area for b2.
    const containers: DroppableContainer[] = [column('A'), column('B'), card('b1', 'B'), card('b2', 'B')];
    const rects = new Map<UniqueIdentifier, ClientRect>([
      ['column-A', rect(A_LEFT, COLUMN_TOP, COLUMN_WIDTH, A_BOTTOM - COLUMN_TOP)],
      ['column-B', rect(B_LEFT, COLUMN_TOP, COLUMN_WIDTH, 120)],
      ['card-b1', rect(B_LEFT + 10, 100, 280, 70)], // y 100..170
      ['card-b2', rect(B_LEFT + 10, 150, 280, 70)], // y 150..220 — overlaps b1 by 20px
    ]);
    const overlapping = {
      active,
      collisionRect: DRAGGED_RECT,
      pointerCoordinates: { x: 470, y: 160 }, // inside the overlap band, which only b2 keeps
      droppableContainers: containers,
      droppableRects: rects,
    };
    // Shrunk to 170..220, b2 would not contain y=160 at all and only b1 would answer.
    expect(boardCollisionDetection(overlapping).map(hit => String(hit.id))).toContain('card-b2');
  });

  it('widens a column that measures exactly zero height, on or off the board', () => {
    // The column skip is on WIDTH, not area, and this is the case that makes the distinction load
    // bearing: a board whose `columnClassName` sets no `min-h` and whose consumer renders no empty
    // placeholder measures an empty column at zero height. It is the whole point of the module that
    // such a column still accepts a drop, so "zero area means hidden" is the wrong simplification.
    const containers: DroppableContainer[] = [column('A'), column('B')];
    const rects = new Map<UniqueIdentifier, ClientRect>([
      ['column-A', rect(A_LEFT, COLUMN_TOP, COLUMN_WIDTH, A_BOTTOM - COLUMN_TOP)],
      ['column-B', rect(B_LEFT, COLUMN_TOP, COLUMN_WIDTH, 0)], // top === bottom
    ]);
    for (let i = 0; i < 20; i++) {
      const id = `a${i + 1}`;
      containers.push(card(id, 'A'));
      rects.set(`card-${id}`, rect(A_LEFT + 10, COLUMN_TOP + i * 100, 280, 80));
    }
    const flat = {
      active,
      collisionRect: DRAGGED_RECT,
      pointerCoordinates: { x: 470, y: 2000 },
      droppableContainers: containers,
      droppableRects: rects,
    };
    expect(boardCollisionDetection(flat)[0]?.id).toBe('column-B');

    // And with a board box measured, where the column clips to `null` (nothing of it is on screen,
    // since it has no height to show). A clipped-away LANE still receives the band and stays a
    // target — only its cards would be dropped, and it has none.
    expect(
      boardCollisionDetection(inBoardBox(flat, rect(A_LEFT, COLUMN_TOP, 640, 2000)))[0]?.id,
    ).toBe('column-B');
  });
});
