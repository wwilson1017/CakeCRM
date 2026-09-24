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
//
// Node env, not jsdom: the file used to opt into jsdom solely for `window.confirm`, and the
// prompt now goes through the app's own `confirmDialog` — which is mocked here, so there is no
// DOM left to need.
import { beforeEach, describe, expect, it, vi } from 'vitest';

interface ConfirmOptions {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
}
const confirmDialog = vi.hoisted(() =>
  vi.fn<(options: ConfirmOptions) => Promise<boolean>>());
vi.mock('../confirm', () => ({ confirmDialog }));

const { CRM_CLOSING_REASONS, confirmDiscardOn, denyEscapeBackdrop } = await import('./closePolicy');

beforeEach(() => {
  confirmDialog.mockReset();
  confirmDialog.mockResolvedValue(true);
});

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
  it('does not prompt for a reason the app guard will refuse anyway', async () => {
    // The ordering this defends: CollectionDetail consults the BODY guard first, so an
    // unconditional confirm would put "Discard unsaved changes?" on screen for an Escape keypress
    // and then decline to close whatever the user answered.
    await expect(confirmDiscardOn('escape', () => true)).resolves.toBe(true);
    await expect(confirmDiscardOn('backdrop', () => true)).resolves.toBe(true);
    expect(confirmDialog).not.toHaveBeenCalled();
  });

  it('prompts on a real close only while dirty, and honours the answer', async () => {
    await expect(confirmDiscardOn('button', () => false)).resolves.toBe(true);
    expect(confirmDialog).not.toHaveBeenCalled();

    confirmDialog.mockResolvedValue(false);
    await expect(confirmDiscardOn('button', () => true)).resolves.toBe(false);

    confirmDialog.mockResolvedValue(true);
    await expect(confirmDiscardOn('nav', () => true)).resolves.toBe(true);
    expect(confirmDialog).toHaveBeenCalledTimes(2);
  });

  it('returns a promise for every branch, so a caller may always await it', () => {
    // `DetailCloseGuard` permits `boolean | Promise<boolean>`, and `CollectionDetail.request`
    // awaits — but a body that composes this into its own guard must be able to await it too,
    // including on the short-circuit paths that never touch the dialog.
    expect(confirmDiscardOn('escape', () => true)).toBeInstanceOf(Promise);
    expect(confirmDiscardOn('button', () => false)).toBeInstanceOf(Promise);
    expect(confirmDiscardOn('button', () => true)).toBeInstanceOf(Promise);
  });

  it('asks with entity-neutral copy — this module is shared by every collection detail', () => {
    // Naming one record type here would ship the wrong sentence to the others; the deals detail
    // was the first consumer and is deliberately not mentioned.
    void confirmDiscardOn('button', () => true);
    const options: ConfirmOptions = confirmDialog.mock.calls[0][0];
    expect(`${options.title} ${options.message}`.toLowerCase()).not.toMatch(/deal|contact|todo|company/);
  });
});
