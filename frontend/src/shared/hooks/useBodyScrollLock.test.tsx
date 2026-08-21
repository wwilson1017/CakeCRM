// @vitest-environment jsdom
//
// Body scroll-lock contract.
//
// WHY THIS FILE EXISTS. Unifying four hand-rolled locks is only a win if the one survivor is
// actually correct, and every property that matters here is invisible to `tsc` and to eslint:
// measurement ORDER, additive-vs-replacing padding, restoring the inline value rather than a
// computed one, and ref-counting that survives out-of-order release. Three of the four original
// copies were wrong about at least one of those, so the survivor gets executable proofs.
//
// jsdom does not do layout, so `documentElement.clientWidth` is stubbed with a getter that REACTS
// to `body.style.overflow` — full width once overflow is hidden, 15px less while it is not, the
// way a classic-scrollbar browser behaves when body overflow propagates to the viewport. A fixed
// stub would be a tautology; a reactive one makes the measurement assertions real.
//
// That default models ONE regime. Because it does, the tests that matter most stub it differently
// per case — overlay scrollbars, and a gutter that survives the lock — so propagation is an INPUT
// here, never a baked-in assumption.
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { acquireBodyScrollLock, useBodyScrollLock } from './useBodyScrollLock';

const VIEWPORT_WIDTH = 1024;
const SCROLLBAR_WIDTH = 15;

/** Every root this test created, so `afterEach` can unmount even after a failed assertion. */
let roots: Root[] = [];
/** Every imperative disposer this test took out, for the same reason. */
let disposers: Array<() => void> = [];
let clientWidthDescriptor: PropertyDescriptor | undefined;

function mount(node: ReactNode): Root {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  roots.push(root);
  act(() => root.render(node));
  return root;
}

function unmount(root: Root) {
  act(() => root.unmount());
  roots = roots.filter(r => r !== root);
}

function acquire(): () => void {
  const release = acquireBodyScrollLock();
  disposers.push(release);
  return release;
}

/** A component whose only job is to hold a lock through the hook. */
function Locker({ locked }: { locked: boolean }) {
  useBodyScrollLock(locked);
  return null;
}

beforeEach(() => {
  window.innerWidth = VIEWPORT_WIDTH;
  clientWidthDescriptor = Object.getOwnPropertyDescriptor(
    Object.getPrototypeOf(document.documentElement),
    'clientWidth',
  );
  // The gutter exists only while the page can scroll — i.e. while overflow is NOT hidden.
  Object.defineProperty(document.documentElement, 'clientWidth', {
    configurable: true,
    get: () =>
      document.body.style.overflow === 'hidden' ? VIEWPORT_WIDTH : VIEWPORT_WIDTH - SCROLLBAR_WIDTH,
  });
});

afterEach(() => {
  // Order matters: React cleanups first (they call disposers), then any stragglers.
  [...roots].forEach(unmount);
  disposers.forEach(release => release());
  disposers = [];
  roots = [];

  delete (document.documentElement as unknown as Record<string, unknown>).clientWidth;
  if (clientWidthDescriptor) {
    Object.defineProperty(
      Object.getPrototypeOf(document.documentElement),
      'clientWidth',
      clientWidthDescriptor,
    );
  }
  document.body.style.overflow = '';
  document.body.style.paddingRight = '';
  document.body.innerHTML = '';
});

describe('scrollbar-gutter compensation', () => {
  it('compensates by exactly the width the lock reclaimed', () => {
    acquire();

    // Reachable only from a before/after pair. Measure only afterwards and both reads see the
    // full width, so the delta is 0 and no padding is applied at all.
    expect(document.body.style.overflow).toBe('hidden');
    expect(document.body.style.paddingRight).toBe(`${SCROLLBAR_WIDTH}px`);
  });

  it('ADDS to the padding the stylesheet already gives body', () => {
    const sheet = document.createElement('style');
    sheet.textContent = 'body { padding-right: 8px; }';
    document.head.appendChild(sheet);

    try {
      acquire();
      expect(document.body.style.paddingRight).toBe(`${8 + SCROLLBAR_WIDTH}px`);
    } finally {
      sheet.remove();
    }
  });

  it('leaves padding alone on overlay-scrollbar platforms (macOS, iOS, Android)', () => {
    // No gutter to reclaim: clientWidth already equals innerWidth.
    Object.defineProperty(document.documentElement, 'clientWidth', {
      configurable: true,
      get: () => VIEWPORT_WIDTH,
    });

    acquire();

    expect(document.body.style.overflow).toBe('hidden');
    expect(document.body.style.paddingRight).toBe('');
  });

  it('leaves padding alone when hiding overflow reclaims nothing', () => {
    // A classic 15px scrollbar that is STILL THERE after the lock. Two real regimes produce this
    // and they are indistinguishable from inside the hook, which is the point:
    //   • `scrollbar-gutter: stable` — the gutter is reserved whether or not the page scrolls.
    //   • `html` itself carrying a non-`visible` overflow — the UA then does not propagate
    //     `body`'s overflow to the viewport, so the scrollbar survives. (Not this codebase:
    //     `index.css` sets no overflow on `:root`/`html`, and neither does Tailwind Preflight.
    //     It is one stylesheet edit away, so the behavior is pinned here.)
    //
    // Nothing is reclaimed, so nothing shifts, so the correct compensation is zero. This is
    // exactly where measuring only the PRE-lock gutter does harm rather than nothing: it reads
    // 15px and pads for a scrollbar still on screen, manufacturing the shift the hook exists to
    // prevent. A before/after delta cannot make that mistake in either regime.
    //
    // What stays broken in the second regime is the LOCK — the page still scrolls. That is true
    // of every `body { overflow: hidden }` lock, predates this hook, and is not what it claims.
    Object.defineProperty(document.documentElement, 'clientWidth', {
      configurable: true,
      get: () => VIEWPORT_WIDTH - SCROLLBAR_WIDTH,
    });

    acquire();

    expect(document.body.style.overflow).toBe('hidden');
    expect(document.body.style.paddingRight).toBe('');
  });
});

describe('restoring the prior value', () => {
  it('restores the prior INLINE values, not computed pixels', () => {
    // Non-empty sentinels on purpose. Asserting a restore to '' would also pass against the very
    // bug this replaces (CRM's shell cleared `overflow` unconditionally), so it proves nothing.
    document.body.style.overflow = 'clip';
    document.body.style.paddingRight = '7px';

    const release = acquire();
    expect(document.body.style.overflow).toBe('hidden');

    release();

    expect(document.body.style.overflow).toBe('clip');
    expect(document.body.style.paddingRight).toBe('7px');
  });

  it('hands padding back to the stylesheet when there was no inline value', () => {
    const release = acquire();
    expect(document.body.style.paddingRight).toBe(`${SCROLLBAR_WIDTH}px`);

    release();

    // '' rather than a pinned '0px' — the stylesheet owns it again.
    expect(document.body.style.paddingRight).toBe('');
    expect(document.body.style.overflow).toBe('');
  });
});

describe('ref-counting across independent lockers', () => {
  it('stays locked when the FIRST acquirer releases first', () => {
    // The ordering that matters. LIFO release is the easy case and passes even against
    // independent per-site save/restore; releasing out of order is what catches it.
    const releaseA = acquire();
    const releaseB = acquire();

    releaseA();

    expect(document.body.style.overflow).toBe('hidden');
    expect(document.body.style.paddingRight).toBe(`${SCROLLBAR_WIDTH}px`);

    releaseB();

    expect(document.body.style.overflow).toBe('');
    expect(document.body.style.paddingRight).toBe('');
  });

  it('shares one counter between the hook and an imperative acquirer', () => {
    // The two APIs are the halves of this design; a per-API counter would unlock here.
    const root = mount(<Locker locked />);
    const releaseImperative = acquire();

    unmount(root);
    expect(document.body.style.overflow).toBe('hidden');

    releaseImperative();
    expect(document.body.style.overflow).toBe('');
  });

  it('treats a doubled release as a no-op rather than freeing a newer lock', () => {
    // A acquires → A releases → B acquires → A's stale cleanup fires again. A counter guarded
    // only by "is it zero" would decrement B's lock here and unlock a live overlay.
    const releaseA = acquire();
    releaseA();

    const releaseB = acquire();
    releaseA();

    expect(document.body.style.overflow).toBe('hidden');

    releaseB();
    expect(document.body.style.overflow).toBe('');
  });
});

describe('useBodyScrollLock(locked)', () => {
  it('mutates nothing while locked is false', () => {
    // Deliberately NOT paired with a sibling "did any effect run?" probe: a sibling effect
    // firing says nothing about whether THIS hook's effect ran, so it would assert a property
    // it cannot see. What actually rules out a no-op hook is the next test, where the same
    // component flips to locked and the page has to respond.
    document.body.style.overflow = 'clip';

    mount(<Locker locked={false} />);

    expect(document.body.style.overflow).toBe('clip');
    expect(document.body.style.paddingRight).toBe('');
  });

  it('acquires on false → true and releases on true → false', () => {
    const root = mount(<Locker locked={false} />);
    expect(document.body.style.overflow).toBe('');

    act(() => root.render(<Locker locked />));
    expect(document.body.style.overflow).toBe('hidden');
    expect(document.body.style.paddingRight).toBe(`${SCROLLBAR_WIDTH}px`);

    act(() => root.render(<Locker locked={false} />));
    expect(document.body.style.overflow).toBe('');
    expect(document.body.style.paddingRight).toBe('');
  });

  it('does not restore while another owner still holds the lock', () => {
    const releaseImperative = acquire();
    const root = mount(<Locker locked />);

    act(() => root.render(<Locker locked={false} />));

    expect(document.body.style.overflow).toBe('hidden');

    releaseImperative();
    expect(document.body.style.overflow).toBe('');
  });

  it('survives a StrictMode-style mount → unmount → mount replay and still restores', () => {
    // React 19 StrictMode replays effects in dev. The final unmount is the load-bearing half of
    // this test: "still locked after the replay" alone cannot detect a leaked count.
    const root = mount(<Locker locked />);
    unmount(root);

    const replayed = mount(<Locker locked />);
    expect(document.body.style.overflow).toBe('hidden');

    unmount(replayed);
    expect(document.body.style.overflow).toBe('');
    expect(document.body.style.paddingRight).toBe('');
  });
});
