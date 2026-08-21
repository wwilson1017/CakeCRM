import { useEffect } from 'react';

/**
 * The one body scroll-lock for the whole app.
 *
 * Four components used to set `overflow: hidden` on `<body>` themselves, each slightly
 * differently, and two of the differences were bugs:
 *
 *   1. **The ~15px sideways jump.** Hiding overflow reclaims the scrollbar gutter, so on a
 *      classic-scrollbar browser (Windows Chrome/Edge/Firefox at their defaults) the whole page
 *      jerks sideways the instant an overlay opens and back when it closes. Only `SearchOverlay`
 *      compensated (the blueprint, measured at +7.50px of shift without it). Overlay-scrollbar platforms
 *      — macOS, iOS, Android — measure 0 here and are left untouched, which is exactly why this
 *      is so easy to miss while developing on a Mac.
 *   2. **Overlays unlocking each other.** `ImageLightbox` and CRM's `DetailPanelShell` each kept
 *      a *private* module-level counter, blind to each other and to the two uncounted sites.
 *      `DetailPanelShell` then cleared `overflow` unconditionally when its own count hit zero —
 *      unlocking the page while some other overlay was still open. Ref-counting only works if
 *      every locker shares ONE counter, which is the counter below.
 *
 * Three ordering rules are load-bearing; all three are why this is centralized rather than
 * copy-pasted:
 *
 *   • Compensate for the width hiding overflow ACTUALLY reclaimed — `clientWidth` after minus
 *     `clientWidth` before — not for the gutter that happened to be there beforehand. Measuring
 *     only one side gets this wrong in both directions: measure only afterwards and the value is
 *     always 0 (the page has already reflowed), so the compensation silently does nothing; measure
 *     only beforehand and you assume hiding overflow always removes the gutter, which
 *     `scrollbar-gutter: stable` breaks by reserving it either way.
 *   • ADD to whatever padding the stylesheet gives `<body>` rather than replacing it, or that
 *     existing padding is silently dropped for as long as the lock is held.
 *   • Restore the prior *inline* value (usually `''`), not a computed pixel value. Restoring `''`
 *     hands control back to the stylesheet; writing a measured number pins it forever.
 *
 * Scope, stated honestly: `overflow: hidden` on `<body>` does NOT stop touch scrolling in iOS
 * Safari, so on iOS the page still scrolls behind an overlay. That is unchanged from before any
 * of this existed — pair the overlay with `overscroll-contain`, which handles scroll *chaining*.
 * Fixing the iOS half properly means `position: fixed` on `<body>` plus save/restore of scrollY,
 * trading a cosmetic gap for a scroll-restoration bug surface.
 */

// ONE shared counter across every locker in the app — see note 2 above. The saved values belong
// to it: they are captured on the 0→1 transition and replayed on the 1→0 transition, so nested
// overlays never save each other's already-locked state as if it were the page's resting state.
let lockCount = 0;
let prevOverflow = '';
let prevPaddingRight = '';

/**
 * Lock body scroll imperatively and get back a disposer that releases exactly this acquisition.
 *
 * Prefer {@link useBodyScrollLock} — reach for this only when the lock has to be sequenced
 * inside an effect that also does something order-dependent (moving focus), or when the decision
 * to lock can only be made *after* layout. Both cases exist in the tree today; see the call sites
 * in `ImageLightbox`, `AgentChatDrawer` and `shared/overlay/DetailModal` (which took over CRM's
 * copy of this pattern in the blueprint).
 *
 * The disposer is **per-acquisition and idempotent**, which is the whole reason this returns a
 * function instead of exposing a bare `release()`. A global release cannot tell whose lock it is
 * releasing, so a stale or doubled cleanup decrements a *newer* owner's lock and unlocks the page
 * out from under a still-open overlay: A acquires, A releases, B acquires, A's cleanup fires a
 * second time → B is unlocked. Latching after the first call makes that a no-op instead. Checking
 * "is the count zero" cannot catch it — at that moment the count is B's, and nonzero.
 */
export function acquireBodyScrollLock(): () => void {
  lockCount += 1;

  if (lockCount === 1) {
    // Measured on BOTH sides of the lock, and the compensation is the DIFFERENCE — the width
    // hiding overflow actually reclaimed. The obvious version measures only beforehand and
    // treats `innerWidth - clientWidth` as the gutter to replace, which is right only while
    // hiding overflow is guaranteed to remove the gutter. It isn't: under
    // `scrollbar-gutter: stable` the gutter is reserved whether or not the page can scroll, so
    // that version measures a gutter that never went away and pads for a second one —
    // manufacturing the very shift this hook exists to prevent. A delta is 0 in that case, 0 on
    // overlay-scrollbar platforms, and the true scrollbar width in the classic case, with no
    // assumption about which regime we are in.
    const widthBefore = document.documentElement.clientWidth;

    prevOverflow = document.body.style.overflow;
    prevPaddingRight = document.body.style.paddingRight;

    document.body.style.overflow = 'hidden';

    const reclaimed = document.documentElement.clientWidth - widthBefore;

    if (reclaimed > 0) {
      // Read from COMPUTED style (so the stylesheet's own padding is included) but saved from
      // INLINE style above, which is the right pair for add-then-restore. The gap it leaves:
      // if something else writes `body.style.paddingRight` inline WHILE a lock is held, the
      // restore below overwrites it with the value from before the lock. No caller does that
      // today; a future "offset the body for a fixed header" effect would be the first, and
      // would need to coordinate here rather than write body padding directly.
      const basePaddingRight = parseFloat(window.getComputedStyle(document.body).paddingRight) || 0;
      document.body.style.paddingRight = `${basePaddingRight + reclaimed}px`;
    }
  }

  let released = false;
  return () => {
    if (released) return;
    released = true;

    // Floored at 0 rather than a bare decrement. Balanced acquire/release cannot go negative on
    // its own — each disposer latches — but a Vite HMR re-evaluation of this module in dev resets
    // the counter to 0 while overlays still hold disposers closed over the OLD module. Those then
    // decrement past zero, and a bare `=== 0` restore condition would never match again, leaving
    // the page `overflow: hidden` with nothing on screen until a reload. The floor makes that
    // self-healing instead of a wedge.
    //
    // The floor would also silently absorb a GENUINE over-release — a real bug, where the page
    // unlocks under a live overlay — so it warns in dev rather than swallowing it. Recovering and
    // reporting are not in conflict; only the reporting half is dropped in production.
    if (import.meta.env.DEV && lockCount - 1 < 0) {
      console.warn(
        '[useBodyScrollLock] release with no lock held. Expected after an HMR reload of this ' +
        'module; otherwise a disposer ran for a lock that was never taken (or was taken by a ' +
        'previous module instance), and an overlay may be left over an unlocked page.',
      );
    }
    lockCount = Math.max(0, lockCount - 1);
    if (lockCount === 0) {
      document.body.style.overflow = prevOverflow;
      document.body.style.paddingRight = prevPaddingRight;
    }
  };
}

/**
 * Lock body scroll while `locked` is true. The API every new overlay should use.
 *
 * Nesting is safe: the page unlocks when the LAST holder releases, not the first.
 */
export function useBodyScrollLock(locked: boolean): void {
  useEffect(() => {
    if (!locked) return;
    // The disposer IS the cleanup — React calls it on unmount and whenever `locked` flips false.
    return acquireBodyScrollLock();
  }, [locked]);
}
