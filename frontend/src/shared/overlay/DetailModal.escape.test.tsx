// @vitest-environment jsdom
//
// `DetailModal`'s Escape semantics, kept separate from DetailModal.test.tsx because these cases
// are about how the modal COMPOSES with the rest of the document — a capture-phase listener that
// claimed the key first, and a second modal stacked on top — rather than about its own rendering.
//
// The blueprint file this came from also exercised a portaled image lightbox rendered from inside
// a panel's React subtree. That component is not part of this port, so those cases are omitted;
// the contract they leaned on (defer when another listener already called `preventDefault()`) is
// still pinned here directly.
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import DetailModal from './DetailModal';

let roots: Root[] = [];
let rectSpy: ReturnType<typeof vi.spyOn>;

function render(node: ReactNode): Root {
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

const pressEscape = () => {
  const e = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
  act(() => { document.body.dispatchEvent(e); });
  return e;
};

beforeEach(() => {
  rectSpy = vi
    .spyOn(Element.prototype, 'getBoundingClientRect')
    .mockImplementation(() => ({ width: 480, height: 600, top: 0, left: 0, right: 480, bottom: 600, x: 0, y: 0, toJSON: () => ({}) }));
});

afterEach(() => {
  [...roots].forEach(unmount);
  roots = [];
  rectSpy.mockRestore();
  document.body.style.overflow = '';
  document.body.style.paddingRight = '';
  document.body.innerHTML = '';
});

describe('DetailModal Escape', () => {
  it('closes on Escape', () => {
    const onClose = vi.fn();
    render(<DetailModal title="Ticket" onClose={onClose}><p>body</p></DetailModal>);
    pressEscape();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('ignores Escape when closeOnEscape is false', () => {
    // The CRM detail surfaces pass false: they hold edit forms with no dirty-close confirm, so an
    // Escape-close there would silently discard typed work.
    const onClose = vi.fn();
    render(<DetailModal title="Deal" onClose={onClose} closeOnEscape={false}><p>body</p></DetailModal>);
    pressEscape();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('defers to a capture-phase listener that already claimed the key', () => {
    // The generic contract every nested overlay relies on.
    const onClose = vi.fn();
    render(<DetailModal title="Ticket" onClose={onClose}><p>body</p></DetailModal>);
    const claim = (e: KeyboardEvent) => { if (e.key === 'Escape') e.preventDefault(); };
    document.addEventListener('keydown', claim, true);
    try {
      pressEscape();
      expect(onClose).not.toHaveBeenCalled();
    } finally {
      document.removeEventListener('keydown', claim, true);
    }
  });

  it('stacked modals: only the topmost answers Escape', () => {
    const outer = vi.fn();
    const inner = vi.fn();
    render(<DetailModal title="Contact" onClose={outer}><p>body</p></DetailModal>);
    const top = render(<DetailModal title="Field settings" onClose={inner}><p>body</p></DetailModal>);
    pressEscape();
    expect(inner).toHaveBeenCalledTimes(1);
    expect(outer).not.toHaveBeenCalled();
    // With the top one gone, the survivor answers.
    unmount(top);
    pressEscape();
    expect(outer).toHaveBeenCalledTimes(1);
  });
});
