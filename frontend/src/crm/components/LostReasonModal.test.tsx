// @vitest-environment jsdom
//
// The Mark-as-Lost reason dialog (issue #128). Four properties, each of which has a
// plausible "simplification" that would silently undo it:
//
//  - The field is a TEXTAREA. A single-line input scrolls a long reason horizontally out of
//    view while it is being typed, which is the defect this whole item exists to fix.
//  - Plain Enter does NOT confirm. Reasons are prose; closing the deal the moment a rep
//    starts a second sentence is the bug, not the feature.
//  - Cmd/Ctrl+Enter confirms exactly ONCE. `service.mark_deal_lost` appends its
//    "Deal lost —" note on every call that finds the deal (a no-op write still returns
//    True), so a second submit writes a second note.
//  - It renders through a portal, because the sheet's z-index:39 root would otherwise trap
//    it under the assistant launcher.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LostReasonModal } from './LostReasonModal';

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(onConfirm = vi.fn(), onCancel = vi.fn()) {
  act(() => {
    root.render(
      <LostReasonModal dealTitle="Wholesale order" onConfirm={onConfirm} onCancel={onCancel} />,
    );
  });
  return { onConfirm, onCancel };
}

// Portal target is document.body, NOT the render container — querying `container` here
// would find nothing and every assertion would vacuously pass.
const field = () => document.body.querySelector('textarea')!;
const button = (label: string) =>
  [...document.body.querySelectorAll('button')]
    .find(b => b.textContent?.trim() === label) as HTMLButtonElement;

function type(text: string) {
  const el = field();
  act(() => {
    // React tracks the last value it set, so assigning `.value` directly makes it ignore
    // the change event. Go through the native setter it patches over.
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!
      .set!.call(el, text);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

function pressEnter(mods: KeyboardEventInit = {}) {
  act(() => {
    field().dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true, ...mods }),
    );
  });
}

describe('LostReasonModal', () => {
  it('renders the reason as a multi-line textarea, not a single-line input', () => {
    render();
    const el = field();
    expect(el).toBeTruthy();
    expect(el.rows).toBe(3);
    // No leftover single-line text input anywhere in the dialog.
    const inputs = [...document.body.querySelectorAll('input')]
      .filter(i => i.type === 'text' || i.type === '');
    expect(inputs).toHaveLength(0);
  });

  it('renders into document.body, escaping the deal sheet stacking context', () => {
    render();
    expect(container.querySelector('textarea')).toBeNull();
    expect(document.body.querySelector('[role="dialog"]')).toBeTruthy();
  });

  it('does NOT confirm on a plain Enter — that inserts a newline', () => {
    const { onConfirm } = render();
    type('Budget');
    pressEnter();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('does not confirm on Enter mid-IME-composition, nor with AltGr', () => {
    // Both guards come from `composerKeyAction`; reusing it is what buys them here, and
    // this pins that the modal really does route through it rather than an inline check.
    const { onConfirm } = render();
    type('日本語');
    pressEnter({ metaKey: true, isComposing: true } as KeyboardEventInit);
    // Windows synthesizes AltGr as Ctrl+Alt.
    pressEnter({ ctrlKey: true, altKey: true });
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('confirms with the typed reason on Cmd+Enter and on Ctrl+Enter', () => {
    const cmd = render(vi.fn());
    type('Chose a competitor');
    pressEnter({ metaKey: true });
    expect(cmd.onConfirm).toHaveBeenCalledWith('Chose a competitor');

    act(() => root.unmount());
    root = createRoot(container);
    const ctrl = render(vi.fn());
    type('Price');
    pressEnter({ ctrlKey: true });
    expect(ctrl.onConfirm).toHaveBeenCalledWith('Price');
  });

  it('confirms only ONCE even if the chord repeats', () => {
    const { onConfirm } = render();
    type('Budget');
    pressEnter({ metaKey: true });
    pressEnter({ metaKey: true });
    pressEnter({ metaKey: true });
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('confirms once via the Mark Lost button, and not again after', () => {
    const { onConfirm } = render();
    type('Budget');
    act(() => { button('Mark Lost').click(); });
    act(() => { button('Mark Lost')?.click(); });
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith('Budget');
  });

  it('confirms with an empty string when nothing was typed', () => {
    // Not `undefined` — that is what routes the write to the mark-lost verb at all.
    const { onConfirm } = render();
    act(() => { button('Mark Lost').click(); });
    expect(onConfirm).toHaveBeenCalledWith('');
  });

  it('cancels on the Cancel button and on Escape, without confirming', () => {
    const { onConfirm, onCancel } = render();
    act(() => { button('Cancel').click(); });
    expect(onCancel).toHaveBeenCalledTimes(1);

    act(() => {
      document.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
      );
    });
    expect(onCancel).toHaveBeenCalledTimes(2);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('traps Tab inside the dialog so focus cannot reach the sheet behind it', () => {
    // `aria-modal` promises an inert background, but the deal sheet underneath is a live
    // DOM subtree — a Shift+Tab out of here lands on its Mark Won button, one keystroke
    // from closing the deal the OPPOSITE way while this dialog is still open.
    render();
    const focusables = [...document.body.querySelectorAll<HTMLElement>(
      '[role="dialog"] textarea, [role="dialog"] button')];
    expect(focusables.length).toBeGreaterThan(1);
    const first = focusables[0];
    const last = focusables[focusables.length - 1];

    // Shift+Tab off the first element wraps to the last, rather than leaving the dialog.
    first.focus();
    act(() => {
      document.dispatchEvent(new KeyboardEvent(
        'keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }));
    });
    expect(document.activeElement).toBe(last);

    // …and Tab off the last wraps back to the first.
    act(() => {
      document.dispatchEvent(new KeyboardEvent(
        'keydown', { key: 'Tab', bubbles: true, cancelable: true }));
    });
    expect(document.activeElement).toBe(first);
  });

  it('pulls focus back in when it is already outside the dialog', () => {
    // The recovery case: something in the sheet behind held focus when the dialog opened.
    const outside = document.createElement('button');
    document.body.appendChild(outside);
    render();
    outside.focus();
    act(() => {
      document.dispatchEvent(new KeyboardEvent(
        'keydown', { key: 'Tab', bubbles: true, cancelable: true }));
    });
    expect(document.body.querySelector('[role="dialog"]')!
      .contains(document.activeElement)).toBe(true);
    outside.remove();
  });

  it('does not yank focus when the parent re-renders with a fresh onCancel', () => {
    // The host passes `onCancel` as an inline arrow, so its identity changes on every
    // parent render — and the deal sheet re-renders on its own detail/activity/touch-count
    // fetches. With `onCancel` as an effect dependency, each of those tore the effect down
    // (restoring focus to whatever held it BEFORE the dialog) and re-armed it (pulling
    // focus back to the textarea), so a background request landing would move a rep off
    // the Cancel button mid-dialog.
    const onConfirm = vi.fn();
    act(() => {
      root.render(
        <LostReasonModal dealTitle="Wholesale order" onConfirm={onConfirm}
          onCancel={() => {}} />,
      );
    });
    const cancel = button('Cancel');
    cancel.focus();
    expect(document.activeElement).toBe(cancel);

    // Re-render with a BRAND NEW onCancel identity, exactly as the host does.
    act(() => {
      root.render(
        <LostReasonModal dealTitle="Wholesale order" onConfirm={onConfirm}
          onCancel={() => {}} />,
      );
    });
    expect(document.activeElement).toBe(button('Cancel'));
  });

  it('still cancels through the LATEST onCancel after a re-render', () => {
    // The other half of reading the callback through a ref: a stale one would call the
    // handler the dialog mounted with, not the one currently passed.
    const stale = vi.fn();
    const fresh = vi.fn();
    act(() => {
      root.render(
        <LostReasonModal dealTitle="Wholesale order" onConfirm={vi.fn()} onCancel={stale} />,
      );
    });
    act(() => {
      root.render(
        <LostReasonModal dealTitle="Wholesale order" onConfirm={vi.fn()} onCancel={fresh} />,
      );
    });
    act(() => {
      document.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
      );
    });
    expect(fresh).toHaveBeenCalledTimes(1);
    expect(stale).not.toHaveBeenCalled();
  });

  it('caps the reason at the length the server accepts', () => {
    render();
    // Mirrors service.MAX_LOST_REASON — past it the route 422s rather than truncating.
    expect(field().maxLength).toBe(500);
  });
});
