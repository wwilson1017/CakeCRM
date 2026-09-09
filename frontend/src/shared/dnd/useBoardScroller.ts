import { useCallback, useRef, type RefCallback, type RefObject } from 'react';

/**
 * A floor, so tall page chrome on a short window cannot compute the board down to a slit.
 * Past the floor the page simply scrolls again, exactly as it did before the board was
 * bounded — a worse scrollbar position, never a broken board.
 */
export const MIN_BOARD_HEIGHT_PX = 320;

/**
 * How tall a board whose top edge sits `documentTop` px down the DOCUMENT must be for its
 * bottom edge to land on the bottom of a `viewportHeight`-tall window.
 *
 * Pure, so the arithmetic is unit-tested without any layout at all — which matters here
 * because jsdom has no layout and the effect around it can therefore only ever be smoke-tested.
 *
 * **Document coordinates, deliberately.** The caller passes
 * `getBoundingClientRect().top + window.scrollY`, not the raw viewport-relative top. The two
 * agree only at `scrollY === 0`, and the document form is the stable one: it does not change
 * when the page scrolls, so no scroll listener is needed and the height cannot chase the scroll
 * position — grow the board as the user scrolls down, which permits more scroll, which grows it
 * again. Our own height write cannot move our own top, which is what keeps the measurement
 * stable under the observers in the hook below.
 *
 * A non-finite input yields the floor rather than propagating NaN into a style string: every
 * comparison against NaN is false, so `Math.max` would hand it straight through.
 */
export function boundedBoardHeight(viewportHeight: number, documentTop: number): number {
  const fill = viewportHeight - documentTop;
  return Number.isFinite(fill) ? Math.max(MIN_BOARD_HEIGHT_PX, fill) : MIN_BOARD_HEIGHT_PX;
}

/**
 * Bound a kanban board's scroller to the bottom of the window, so its scrollbars are always on
 * screen (issue #129, adapted from the blueprint's `useBoardScroller`).
 *
 * ## The bug
 *
 * `KanbanBoard` renders ONE element that is both the horizontal scroller and an ordinary in-flow
 * block as tall as its tallest column. A native horizontal scrollbar sits at the bottom edge of
 * *its own box*, not the viewport — so on a busy board the only way to scroll sideways was to
 * scroll the page down past the end of the longest column first, and then back up. CakeCRM
 * masked it with `max-h-[70vh] overflow-y-auto` on each COLUMN, which merely moved the bar to
 * ~70vh + header below the page top: still off screen, and at the cost of a nested scroll region
 * per lane (the reason `collision.ts` has to clip card rects to their column at all).
 *
 * ## Why the height is measured rather than written in CSS
 *
 * The board wants "from wherever I start, down to the bottom of the window", and CSS cannot say
 * that: `calc(100dvh - ?)` needs the board's own distance from the top of the page and no unit
 * carries it. The alternative is a real `height:100%` / `min-h-0` flex chain from `html` down —
 * but this app does not have one and cannot cheaply grow one: `index.css` sets only
 * `#root { min-height: 100svh }`, so `CrmLayout`'s `height: '100%'` resolves to `auto` and its
 * `overflow: 'auto'` region never scrolls (the DOCUMENT scrolls; verified in a browser). Fixing
 * that would change the scroll model of every CRM page at once, which is a far larger change
 * than #129 asks for. Measuring keeps the whole fix inside the one page that has the problem.
 *
 * ## Two deliberate simplifications from the blueprint
 *
 * • **No visual-viewport primitive.** The blueprint reads `window.visualViewport` through a
 *   shared `inFlowShellHeight` helper so an iOS keyboard shrinks the board instead of hiding the
 *   scrollbar beneath itself. This hook is DESKTOP-ONLY (see below), which makes the keyboard
 *   case moot, so `window.innerHeight` is the honest input and CakeCRM does not grow a viewport
 *   primitive with one consumer. What is lost: under desktop pinch-zoom the board is bounded to
 *   the LAYOUT viewport, so the scrollbar can sit outside the zoomed-in view until zoom-out —
 *   which self-heals, and is what every other layout in this app already does.
 * • **A callback ref, not a layout effect.** The blueprint's hook lives inside its board
 *   component; this one is called by the PAGE, which does not render the scroller directly and
 *   has several ways not to render it at all (a loading early-return, the List/Board view
 *   switch, an empty corpus, a filter matching nothing). A callback ref fires exactly when the
 *   node attaches or detaches, however the page got there, so there is no "has the board mounted
 *   yet" dependency arithmetic to keep correct.
 *
 * ## Deliberate limits
 *
 * • **Mobile is left alone** — pass `bounded: false`. Touch platforms draw no persistent
 *   scrollbar to reach, and the mobile board is a `w-[85vw] snap-x` swipe with a stage chip bar;
 *   bounding it would only shrink it, since the chrome above it can be a third of the viewport.
 * • `bounded: false` still tracks the node and clears any inline height, so a resize across the
 *   mobile breakpoint gives the board back its in-flow height in the same commit that gives it
 *   the mobile layout.
 *
 * Returns the callback to hand the board as its `scrollerRef`, plus an object ref holding the
 * same node for anything else that needs it (the page's mobile `IntersectionObserver` root).
 * One element, both uses.
 */
export function useBoardScroller(bounded: boolean): {
  ref: RefCallback<HTMLDivElement>;
  node: RefObject<HTMLDivElement | null>;
} {
  const node = useRef<HTMLDivElement | null>(null);

  const ref = useCallback<RefCallback<HTMLDivElement>>(el => {
    node.current = el;
    // React 19 calls a ref callback with `null` only when the previous call returned no
    // cleanup. Both non-null branches below DO return one, so this branch is the legacy
    // detach path — kept because the contract allows it and forgetting it would strand
    // `node.current` on a removed element.
    if (!el) return;

    if (!bounded) {
      el.style.height = '';
      return () => {
        node.current = null;
      };
    }

    const apply = () => {
      const documentTop = el.getBoundingClientRect().top + window.scrollY;
      el.style.height = `${boundedBoardHeight(window.innerHeight, documentTop)}px`;
    };
    apply();

    // Anything that changes the page's height can have moved the board's top — a notice
    // appearing above it, the filter bar wrapping, a lane finishing its load. Observing
    // `document.body` catches all of it without this hook knowing what the page puts above its
    // board, and it settles in one pass: the height we write feeds back as a body resize,
    // recomputes to the same value, and writes the same string, which is no size change and so
    // no further notification.
    //
    // Feature-guarded because jsdom implements no ResizeObserver and this repo has no global
    // test setup file — a test rendering a board must not have to know that. Without it the
    // board still measures on attach and on window resize, which covers the common case in
    // every real browser (ResizeObserver has been baseline since 2020).
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(apply) : null;
    observer?.observe(document.body);
    window.addEventListener('resize', apply);

    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', apply);
      // Clear rather than leave the last value: the node can outlive this callback (a
      // `bounded` flip re-attaches the same element), and a stale explicit height on an
      // unbounded board is exactly the bug this hook exists to avoid, inverted.
      el.style.height = '';
      node.current = null;
    };
  }, [bounded]);

  return { ref, node };
}
