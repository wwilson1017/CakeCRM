// @vitest-environment jsdom
//
// jsdom has no layout: every `getBoundingClientRect()` is all zeros and there is no
// ResizeObserver. So the arithmetic is tested PURELY (`boundedBoardHeight`), and the hook itself
// is tested only for the things that do not need layout — that it writes a height at all, that it
// reads DOCUMENT coordinates, that attach/detach and the `bounded` flip clean up after themselves,
// and that it survives a platform with no ResizeObserver. Whether the resulting board actually
// reaches the bottom of a real window is the evidence run's question, not this file's.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { boundedBoardHeight, MIN_BOARD_HEIGHT_PX, useBoardScroller } from './useBoardScroller';

describe('boundedBoardHeight', () => {
  it('fills from the board top to the bottom of the window', () => {
    expect(boundedBoardHeight(900, 240)).toBe(660);
  });

  it('floors, so tall chrome on a short window cannot compute the board down to a slit', () => {
    expect(boundedBoardHeight(600, 500)).toBe(MIN_BOARD_HEIGHT_PX);
    // Past the floor the page simply scrolls again, as it did before the board was bounded.
    expect(boundedBoardHeight(400, 900)).toBe(MIN_BOARD_HEIGHT_PX);
  });

  it('yields the floor rather than propagating NaN into a style string', () => {
    // Every comparison against NaN is false, so `Math.max` alone would hand it straight through
    // and the board would be styled `height: NaNpx` — no height at all, silently.
    expect(boundedBoardHeight(NaN, 0)).toBe(MIN_BOARD_HEIGHT_PX);
    expect(boundedBoardHeight(900, NaN)).toBe(MIN_BOARD_HEIGHT_PX);
    expect(boundedBoardHeight(Infinity, Infinity)).toBe(MIN_BOARD_HEIGHT_PX);
  });
});

describe('useBoardScroller', () => {
  let host: HTMLDivElement;
  let root: Root;
  let rectTop = 0;
  const originalRect = HTMLElement.prototype.getBoundingClientRect;

  function Probe({ bounded }: { bounded: boolean }) {
    const scroller = useBoardScroller(bounded);
    return <div data-testid="board" ref={scroller.ref} />;
  }

  function board(): HTMLElement {
    const el = host.querySelector<HTMLElement>('[data-testid="board"]');
    if (!el) throw new Error('board not rendered');
    return el;
  }

  beforeEach(() => {
    rectTop = 0;
    // jsdom returns all zeros; stub on the PROTOTYPE so the element the hook happens to receive
    // is covered without the test needing a handle on it first.
    HTMLElement.prototype.getBoundingClientRect = function getBoundingClientRect() {
      return { top: rectTop, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
    };
    window.innerHeight = 900;
    window.scrollY = 0;
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    HTMLElement.prototype.getBoundingClientRect = originalRect;
  });

  it('bounds the scroller to the bottom of the window on attach', () => {
    rectTop = 240;
    act(() => root.render(<Probe bounded />));
    expect(board().style.height).toBe('660px');
  });

  it('measures in DOCUMENT coordinates, so the height cannot chase the scroll position', () => {
    // The board's top is 60px ABOVE the viewport because the page is scrolled 300px. A
    // viewport-relative reading would compute 960px — taller than the window — which would permit
    // more page scroll, which would grow it again. The document reading is stable: 240 either way.
    rectTop = -60;
    window.scrollY = 300;
    act(() => root.render(<Probe bounded />));
    expect(board().style.height).toBe('660px');
  });

  it('writes no height when it is not bounded, and gives the height back on a flip', () => {
    rectTop = 240;
    act(() => root.render(<Probe bounded />));
    expect(board().style.height).toBe('660px');

    // The mobile breakpoint crossing: the same element, a new callback identity. React runs the
    // previous cleanup and re-attaches, so the board gets its in-flow height back in the same
    // commit that gives it the mobile layout.
    act(() => root.render(<Probe bounded={false} />));
    expect(board().style.height).toBe('');

    act(() => root.render(<Probe bounded />));
    expect(board().style.height).toBe('660px');
  });

  it('re-measures on a window resize', () => {
    rectTop = 240;
    act(() => root.render(<Probe bounded />));
    expect(board().style.height).toBe('660px');

    window.innerHeight = 700;
    act(() => {
      window.dispatchEvent(new Event('resize'));
    });
    expect(board().style.height).toBe('460px');
  });

  it('clears the height and drops its listener on unmount', () => {
    rectTop = 240;
    act(() => root.render(<Probe bounded />));
    const el = board();
    const removeListener = vi.spyOn(window, 'removeEventListener');

    act(() => root.render(<div />));

    expect(el.style.height).toBe('');
    expect(removeListener).toHaveBeenCalledWith('resize', expect.any(Function));
    removeListener.mockRestore();
  });

  it('still bounds the board where the platform has no ResizeObserver', () => {
    // The guard is not belt-and-braces: jsdom implements none and this repo has no global test
    // setup file, so an unguarded `new ResizeObserver(...)` would throw on every board render in
    // every existing suite. Asserted here rather than left to those suites failing obscurely.
    expect(typeof (globalThis as { ResizeObserver?: unknown }).ResizeObserver).toBe('undefined');
    rectTop = 100;
    act(() => root.render(<Probe bounded />));
    expect(board().style.height).toBe('800px');
  });

  describe('where the platform has a ResizeObserver', () => {
    const observed: Element[] = [];
    let trigger: (() => void) | null = null;

    beforeEach(() => {
      observed.length = 0;
      trigger = null;
      class StubResizeObserver {
        constructor(callback: () => void) {
          trigger = callback;
        }
        observe(target: Element) {
          observed.push(target);
        }
        disconnect() {
          trigger = null;
        }
      }
      (globalThis as { ResizeObserver?: unknown }).ResizeObserver = StubResizeObserver;
    });

    afterEach(() => {
      delete (globalThis as { ResizeObserver?: unknown }).ResizeObserver;
    });

    it('re-measures when anything changes the page height above the board', () => {
      rectTop = 240;
      act(() => root.render(<Probe bounded />));
      expect(observed).toContain(document.body);
      expect(board().style.height).toBe('660px');

      // A notice appears above the board and pushes it down.
      rectTop = 340;
      act(() => trigger?.());
      expect(board().style.height).toBe('560px');
    });

    it('disconnects the observer on unmount', () => {
      act(() => root.render(<Probe bounded />));
      expect(trigger).not.toBeNull();
      act(() => root.render(<div />));
      expect(trigger).toBeNull();
    });
  });
});
