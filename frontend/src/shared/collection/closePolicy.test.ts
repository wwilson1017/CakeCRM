// @vitest-environment jsdom
//
// (jsdom only for `window.confirm` — the policy itself is pure. Opted in per file, as the repo
// requires, so the node-env suites keep their speed.)
//
// CRM's detail close POLICY.
//
// This is where the deleted `DetailPanelShell.test.tsx` went. That suite pinned four things; two
// were about the shared modal's presentation ("renders the centred modal with title and header
// actions", "LinkRow renders a tappable link and nothing for a blank value") and are covered by
// `shared/overlay/DetailModal.test.tsx` and the primitives themselves. The other two were its
// DISMISSAL policy — "Escape does NOT close" and "a backdrop click does NOT close" — and those are
// what this file re-pins.
//
// They can be unit assertions now rather than DOM ones because the mechanism and the policy
// separated: `shared/collection`'s `CollectionDetail` owns the mechanism (it asks the body guard,
// then the app guard, and only then acts — pinned by `CollectionDetail.test.tsx`, including that
// `onRequestClose` receives the true reason for every leave path and that its verdict decides).
// What is left for CRM to own is the VERDICT, which is this pure function. App policy × layer
// mechanism reconstructs the old coverage.
import { describe, expect, it, vi } from 'vitest';
import { CRM_CLOSING_REASONS, confirmDiscardOn, denyEscapeBackdrop } from './closePolicy';

describe('denyEscapeBackdrop', () => {
  it('refuses Escape and a backdrop click, allows the × button and ‹ › navigation', () => {
    // The two refusals are the re-pinned DetailPanelShell assertions.
    expect(denyEscapeBackdrop('escape')).toBe(false);
    expect(denyEscapeBackdrop('backdrop')).toBe(false);
    expect(denyEscapeBackdrop('button')).toBe(true);
    expect(denyEscapeBackdrop('nav')).toBe(true);
  });

  it('names both refusals explicitly, because supplying a guard replaces the layer default', () => {
    // CollectionDetail's built-in default already denies `backdrop`; a guard that mentioned only
    // `escape` would silently re-enable it, since the app guard REPLACES rather than layers.
    expect([...CRM_CLOSING_REASONS]).toEqual(['button', 'nav']);
  });
});

describe('confirmDiscardOn', () => {
  it('does not prompt for a reason the app guard will refuse anyway', () => {
    // The ordering this defends: CollectionDetail consults the BODY guard first, so an
    // unconditional confirm would put "Discard unsaved changes?" on screen for an Escape keypress
    // and then decline to close whatever the user answered.
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    try {
      expect(confirmDiscardOn('escape', () => true)).toBe(true);
      expect(confirmDiscardOn('backdrop', () => true)).toBe(true);
      expect(confirm).not.toHaveBeenCalled();
    } finally {
      confirm.mockRestore();
    }
  });

  it('prompts on a real close only while dirty, and honours the answer', () => {
    const confirm = vi.spyOn(window, 'confirm');
    try {
      confirm.mockClear();
      expect(confirmDiscardOn('button', () => false)).toBe(true);
      expect(confirm).not.toHaveBeenCalled();

      confirm.mockReturnValue(false);
      expect(confirmDiscardOn('button', () => true)).toBe(false);
      confirm.mockReturnValue(true);
      expect(confirmDiscardOn('nav', () => true)).toBe(true);
      expect(confirm).toHaveBeenCalledTimes(2);
    } finally {
      confirm.mockRestore();
    }
  });
});
