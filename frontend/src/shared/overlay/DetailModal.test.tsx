// @vitest-environment jsdom
//
// The shared detail overlay.
//
// WHAT THIS FILE INHERITS. It is the consolidation of three suites that used to pin four
// near-duplicate shells:
//
//  - the `dock:`-vs-`md:` decision-rule block from `the blueprint's detail-shell test`
// and `the blueprint's card-overlay-shell test`, re-pinned against the
//    centred-modal structure;
// - the scroll-lock gating block from `apps/crm/.../DetailPanelShell.test.tsx`, whose
//    post-layout visibility measurement is still load-bearing — `CrmPage` keeps inactive tabs
//    mounted at `display:none`, so a mounted panel is not necessarily an on-screen one.
//
// DELETED WITH THEIR SUBJECT, deliberately and not silently:
// - the 11-case `desktop dock offset` describe — the `--panel-top` measurement it
//    pinned is gone, because a centred modal never docks below the header;
//  - `never locks on desktop, where the panel is a side overlay …` — inverted below, since a
//    blocking modal must lock at every size;
//  - `releases when the viewport widens past the mobile breakpoint mid-open` — there is no JS
//    breakpoint left to cross; the gate is width>0 visibility, and the hidden/visible
//    transitions below cover it.
//
// jsdom lays nothing out and evaluates no media queries, so every `dock:` assertion here is a
// decision-RULE pin on class strings — exactly as the suites it replaces were. Real geometry is
// proven on the PR's evidence run.
import { StrictMode, act, useState, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import DetailModal from './DetailModal';
import { acquireBodyScrollLock } from '../hooks/useBodyScrollLock';

let roots: Root[] = [];
let panelWidth = 0;
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

const wrapperEl = () => document.querySelector('.fixed.inset-0') as HTMLElement;
const panelEl = () => document.querySelector('[role="dialog"]') as HTMLElement;
const tokens = (el: HTMLElement) => el.className.split(/\s+/);

const modal = (props: Partial<Parameters<typeof DetailModal>[0]> = {}) => (
  <DetailModal title="Acme Corp" onClose={() => {}} {...props}>
    <p>body</p>
  </DetailModal>
);

/**
 * Present every element as rendered.
 *
 * The trap filters focusables to elements with client rects, because its own Back/× chrome is
 * `dock:`-gated and `.focus()` on a `display:none` element is a silent no-op. jsdom lays
 * nothing out, so `getClientRects()` is empty for EVERYTHING there — meaning without this stub
 * the filter legitimately finds zero controls and every wrap assertion tests the empty-list
 * fallback instead of the wrap. Restore it in a `finally`.
 */
function allControlsRendered() {
  return vi.spyOn(Element.prototype, 'getClientRects')
    .mockImplementation(() => [{ width: 10, height: 10 }] as unknown as DOMRectList);
}

/**
 * Elements to report as NOT rendered, whatever `panelWidth` says.
 *
 * `panelWidth` is a single global, which is enough for "is this one panel on screen", but it
 * cannot express the CRM shape — one modal hidden by a tab switch while another is visible.
 * Add an element here to hide just that one.
 */
let hiddenEls: Set<Element> = new Set();

beforeEach(() => {
  panelWidth = 480;
  hiddenEls = new Set();
  // jsdom lays nothing out, so every element measures 0. Drive the visibility probe from
  // variables the test controls; `display:none` is what makes this 0 in the real app.
  rectSpy = vi
    .spyOn(Element.prototype, 'getBoundingClientRect')
    .mockImplementation(function (this: Element) {
      const w = hiddenEls.has(this) ? 0 : panelWidth;
      return { width: w, height: w ? 600 : 0, top: 0, left: 0, right: w, bottom: 600, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
    });
});

afterEach(() => {
  [...roots].forEach(unmount);
  roots = [];
  rectSpy.mockRestore();
  document.body.style.overflow = '';
  document.body.style.paddingRight = '';
  document.body.innerHTML = '';
});

// ---------------------------------------------------------------------------------------------
describe('DetailModal takeover vs centred modal', () => {
  const DOCK_DECIDING_WRAPPER = ['dock:flex', 'dock:items-center', 'dock:justify-center', 'dock:bg-black/50', 'dock:p-4'];
  const DOCK_DECIDING_PANEL = ['dock:h-auto', 'dock:max-w-2xl', 'dock:rounded-xl', 'dock:border', 'dock:border-line'];

  it('gates the centred modal on the height-aware dock: variant, not width-only md:', () => {
    render(modal());
    const w = tokens(wrapperEl());
    const p = tokens(panelEl());
    DOCK_DECIDING_WRAPPER.forEach(c => expect(w).toContain(c));
    DOCK_DECIDING_PANEL.forEach(c => expect(p).toContain(c));
  });

  it('leaves no width-only class on the wrapper or the panel root at all', () => {
    render(modal());
    expect(tokens(wrapperEl()).filter(c => c.startsWith('md:'))).toEqual([]);
    expect(tokens(panelEl()).filter(c => c.startsWith('md:'))).toEqual([]);
  });

  it('leaves the BASE state an unconstrained full-screen takeover', () => {
    render(modal());
    const w = tokens(wrapperEl());
    const p = tokens(panelEl());
    // The wrapper covers the viewport but must not paint or centre anything until `dock:`.
    expect(w).toContain('fixed');
    expect(w).toContain('inset-0');
    ['flex', 'items-center', 'justify-center', 'bg-black/50', 'p-4'].forEach(c => expect(w).not.toContain(c));
    // The panel fills that wrapper, with no bare constraint that would shrink it on a phone.
    expect(p).toContain('h-full');
    expect(p).toContain('w-full');
    expect(p.some(c => /^(max-w-|rounded-|border$|left-auto|right-0|top-)/.test(c))).toBe(false);
  });

  it("keeps the header's VERTICAL padding on dock: and its horizontal padding on md:", () => {
    render(modal());
    const header = tokens(panelEl().firstElementChild as HTMLElement);
    expect(header).toContain('dock:py-4');
    expect(header).not.toContain('md:py-4');
    expect(header).toContain('md:px-6');
  });

  it('flips the Back arrow and the × with the presentation, never independently', () => {
    render(modal());
    const back = document.querySelector('[aria-label="Back"]') as HTMLElement;
    const close = document.querySelector('[aria-label="Close"]') as HTMLElement;
    // Back is visible on the takeover, hidden once centred.
    expect(tokens(back)).toContain('dock:hidden');
    expect(tokens(back)).not.toContain('hidden');
    expect(tokens(back).filter(c => c.startsWith('md:'))).toEqual([]);
    // × is the mirror image. Splitting these two strands a landscape phone with no way out.
    expect(tokens(close)).toContain('hidden');
    expect(tokens(close)).toContain('dock:flex');
    expect(tokens(close).filter(c => c.startsWith('md:'))).toEqual([]);
  });

  it('gives the desktop × a real hit area and resting contrast, not a hover-only glyph', () => {
 // the blueprint. Both halves were the reported complaint: it was a bare `text-xl` character with no
    // padding beyond the glyph, and `text-muted` until hovered. `p-2.5` around a 24px icon is a
    // 44px square — the tap target `shared/media/ImageLightbox.tsx` documents, which applies
    // because `dock:` admits an iPad in portrait, not only a mouse.
    render(modal());
    const close = tokens(document.querySelector('[aria-label="Close"]') as HTMLElement);
    expect(close).toContain('p-2.5');
    expect(close).toContain('text-charcoal');
    expect(close).not.toContain('text-muted');
    // The negative margin is what keeps the icon flush with the header's padding edge while
    // that padding grows the tap target outward, and the hover BACKGROUND is what replaced the
    // hover-only color change — drop either and the button silently regresses to the old feel.
    expect(close).toContain('-mr-2.5');
    expect(close).toContain('hover:bg-sand');
  });

  it('bounds the centred height in dvh, not %, so an iPad in portrait clears the iOS toolbar', () => {
    render(modal());
    expect(tokens(panelEl())).toContain('dock:max-h-[calc(100dvh-2rem)]');
  });

  it('keeps the panel itself the scroll container, so a sticky close-out bar can ride its bottom edge', () => {
    render(modal());
    // a sibling surface's guided close-out bar is `sticky bottom-0` as the LAST CHILD of the body.
 // If the scrollport ever moves to an inner div, it silently stops sticking.
    expect(tokens(panelEl())).toContain('overflow-y-auto');
    expect(tokens(panelEl().firstElementChild as HTMLElement)).toContain('sticky');
  });
});

// ---------------------------------------------------------------------------------------------
describe('DetailModal body scroll lock', () => {
  it('locks while a visible modal is open, and releases on close', () => {
    const root = render(modal());
    expect(document.body.style.overflow).toBe('hidden');
    unmount(root);
    expect(document.body.style.overflow).toBe('');
  });

  it('does NOT lock a modal mounted inside a hidden tab', () => {
    // CrmPage keeps inactive tabs mounted; a panel nobody can see must not lock the page.
    panelWidth = 0;
    render(modal());
    expect(document.body.style.overflow).toBe('');
  });

  it('releases when a visible modal becomes hidden without unmounting', () => {
    // The exact CRM tab-switch case: open a contact, switch tabs. Still mounted, now invisible.
    function Harness() {
      const [, force] = useState(0);
      return (
        <>
          <button onClick={() => force(n => n + 1)}>rerender</button>
          {modal()}
        </>
      );
    }
    render(<Harness />);
    expect(document.body.style.overflow).toBe('hidden');
    panelWidth = 0;
    act(() => { (document.querySelector('button') as HTMLElement).click(); });
    expect(document.body.style.overflow).toBe('');
  });

  it('re-locks when a hidden modal becomes visible again', () => {
    function Harness() {
      const [, force] = useState(0);
      return (
        <>
          <button onClick={() => force(n => n + 1)}>rerender</button>
          {modal()}
        </>
      );
    }
    panelWidth = 0;
    render(<Harness />);
    expect(document.body.style.overflow).toBe('');
    panelWidth = 480;
    act(() => { (document.querySelector('button') as HTMLElement).click(); });
    expect(document.body.style.overflow).toBe('hidden');
  });

  it('does not re-acquire — or leak — a lock across re-renders while visibility is unchanged', () => {
    // Re-acquiring would re-run the shared module's two layout-forcing clientWidth reads on
    // every keystroke in an open panel. The ref latch is what prevents it.
    const widthReads = vi.fn();
    const originalWidth = Object.getOwnPropertyDescriptor(Element.prototype, 'clientWidth');
    Object.defineProperty(Element.prototype, 'clientWidth', {
      configurable: true,
      get() { widthReads(); return 1000; },
    });
    function Harness() {
      const [, force] = useState(0);
      return (
        <>
          <button onClick={() => force(n => n + 1)}>rerender</button>
          {modal()}
        </>
      );
    }
    try {
      render(<Harness />);
      const afterMount = widthReads.mock.calls.length;
      act(() => { (document.querySelector('button') as HTMLElement).click(); });
      act(() => { (document.querySelector('button') as HTMLElement).click(); });
      expect(widthReads.mock.calls.length).toBe(afterMount);
      expect(document.body.style.overflow).toBe('hidden');
    } finally {
      if (originalWidth) Object.defineProperty(Element.prototype, 'clientWidth', originalWidth);
    }
  });

  it('stays balanced through a StrictMode effect replay', () => {
    // StrictMode mounts, tears down and remounts effects. An unbalanced latch shows up here as
    // a lock that never releases, or one released while still open.
    const outer = acquireBodyScrollLock();
    const root = render(<StrictMode>{modal()}</StrictMode>);
    expect(document.body.style.overflow).toBe('hidden');
    unmount(root);
    // The outside lock is still held, so the page must STAY locked.
    expect(document.body.style.overflow).toBe('hidden');
    outer();
    expect(document.body.style.overflow).toBe('');
  });

  it('locks on desktop too — a blocking modal never leaves the page scrollable behind its backdrop', () => {
    // Replaces the old `never locks on desktop` case. The premise inverted with the
    // presentation: a right-hand dock left the board usable, a centred modal does not.
    panelWidth = 1200;
    render(modal());
    expect(document.body.style.overflow).toBe('hidden');
  });

  it('keeps the page locked while a second modal is stacked on top', () => {
    const first = render(modal());
    const second = render(modal({ title: 'Stacked' }));
    unmount(second);
    expect(document.body.style.overflow).toBe('hidden');
    unmount(first);
    expect(document.body.style.overflow).toBe('');
  });

  it('a modal hidden by a tab switch leaves the stack — closing the visible one does not focus it', () => {
    // The CRM cross-entity navigation shape, and a real defect before this was visibility-keyed:
    // `CrmContext.navigateToCompany` only flips `activeTab`, and `CrmPage` hides inactive tabs
    // with `display:none` WITHOUT unmounting them. So the contact modal is still mounted, and
    // invisible, while a company modal opens on another tab. A mount-keyed stack left the
    // hidden contact as the "survivor", and focusing a `display:none` element fails silently —
    // dropping focus to <body>.
    const trigger = document.createElement('button');
    document.body.appendChild(trigger);
    trigger.focus();

    // The contact modal, visible.
    const onContactClose = vi.fn();
    function Hideable({ hidden }: { hidden: boolean }) {
      return (
        <div className={hidden ? 'hidden' : undefined}>
          {modal({ title: 'Contact', onClose: onContactClose })}
        </div>
      );
    }
    const contact = render(<Hideable hidden={false} />);
    const contactPanel = panelEl();
    expect(document.activeElement).toBe(contactPanel);

    // Tab switch: the contact modal stays mounted but stops rendering. Hiding THIS element
    // only — the whole point is that another modal is visible at the same time.
    hiddenEls.add(contactPanel);
    act(() => contact.render(<Hideable hidden />));

    // The company modal opens on the now-active tab.
    const company = render(modal({ title: 'Company' }));
    const companyPanel = Array.from(document.querySelectorAll('[role="dialog"]'))
      .find(el => el.textContent?.includes('Company')) as HTMLElement;
    expect(document.activeElement).toBe(companyPanel);

    // Close it. Focus must NOT land on the hidden contact modal — that is a silent no-op in a
    // browser, which drops focus to <body> and disarms the Tab trap with it.
    unmount(company);
    expect(document.activeElement).not.toBe(contactPanel);

    // And the membership half, which the focus assertion above CANNOT prove on its own:
    // `focusSurvivor` skips unrendered entries as belt-and-braces, so it stays correct even if
    // the hidden modal is wrongly left in the stack. Escape is membership-only — a modal that
    // is still in the stack is topmost now that the company is gone, and would close while the
    // user cannot see it.
    const e = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    act(() => { document.body.dispatchEvent(e); });
    expect(onContactClose).not.toHaveBeenCalled();
  });

  it('hands focus to the SURVIVING modal when a stacked one closes, not back to the trigger', () => {
    // CRM stacks FieldSettingsPanel over a contact detail. Returning focus to our own trigger
    // would park it behind the still-open dialog, outside its Tab trap — and the survivor
    // cannot fix that itself, because splicing the module-level stack re-renders nobody.
    // Pinned because mutation testing showed `survivor.el?.focus()` could be no-opped with the
    // whole suite still green.
    const trigger = document.createElement('button');
    document.body.appendChild(trigger);
    trigger.focus();

    render(modal({ title: 'Contact' }));
    const survivor = panelEl();
    const top = render(modal({ title: 'Field settings' }));
    unmount(top);

    expect(document.activeElement).toBe(survivor);
    expect(document.activeElement).not.toBe(trigger);
  });
});

// ---------------------------------------------------------------------------------------------
describe('DetailModal backdrop', () => {
  /**
   * A complete pointer gesture, dispatched with REAL browser semantics: press on `down`,
   * release on `up`, and the `click` on their NEAREST COMMON ANCESTOR — never on `up`
   * directly. That last part is load-bearing: an earlier version dispatched the click on the
   * release element, which only matches the browser when `up` is an ancestor of `down` — so
   * the backdrop-press → panel-release case exercised a click the browser never fires and
   * passed while the component was broken (jsdom does not synthesize `click` from
   * mousedown/mouseup, so the helper must model the targeting rule itself).
   */
  function commonAncestor(a: HTMLElement, b: HTMLElement): HTMLElement {
    let node: HTMLElement | null = a;
    while (node && !node.contains(b)) node = node.parentElement;
    return node ?? a;
  }
  function gesture(down: HTMLElement, up: HTMLElement) {
    act(() => {
      down.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      up.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
      commonAncestor(down, up).dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
  }

  it('absorbs a backdrop click by default — onClose is NOT called', () => {
    // The backdrop is a click shield first and a dimming artefact second. Most of these
    // surfaces carry an edit form.
    const onClose = vi.fn();
    render(modal({ onClose }));
    gesture(wrapperEl(), wrapperEl());
    expect(onClose).not.toHaveBeenCalled();
  });

  it('invokes onBackdropClick when a read-only surface opts in', () => {
    const onBackdropClick = vi.fn();
    render(modal({ onBackdropClick }));
    gesture(wrapperEl(), wrapperEl());
    expect(onBackdropClick).toHaveBeenCalledTimes(1);
  });

  it('never dismisses from a click inside the panel', () => {
    const onBackdropClick = vi.fn();
    render(modal({ onBackdropClick }));
    gesture(panelEl(), panelEl());
    expect(onBackdropClick).not.toHaveBeenCalled();
  });

  it('does not dismiss when a drag STARTS in the panel and releases on the backdrop', () => {
    // Selecting text in the panel and releasing over the dim area. The browser fires `click` on
    // the nearest common ancestor — which is the wrapper, because the centred geometry makes it
    // the panel's PARENT (the old kanban backdrop was a sibling, so this could not happen).
    const onBackdropClick = vi.fn();
    render(modal({ onBackdropClick }));
    gesture(panelEl(), wrapperEl());
    expect(onBackdropClick).not.toHaveBeenCalled();
  });

  it('does not dismiss when a drag STARTS on the backdrop and releases in the panel', () => {
    // The click for this gesture fires ON THE WRAPPER (common ancestor), so the click-target
    // check passes and the panel's stopPropagation never runs — only the mouseup ref can
    // block it. A near-miss press on the dim margin released on a panel button must not
    // close the panel (and with the old single-ref guard, it did).
    const onBackdropClick = vi.fn();
    render(modal({ onBackdropClick }));
    gesture(wrapperEl(), panelEl());
    expect(onBackdropClick).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------------------------
describe('DetailModal focus and announcement', () => {
  it('focuses the dialog root on open and restores the trigger on close', () => {
    const trigger = document.createElement('button');
    document.body.appendChild(trigger);
    trigger.focus();
    expect(document.activeElement).toBe(trigger);

    const root = render(modal());
    expect(document.activeElement).toBe(panelEl());
    unmount(root);
    expect(document.activeElement).toBe(trigger);
  });

  it('announces as role=dialog labelled by its title, and deliberately does NOT claim aria-modal', () => {
    // `aria-modal="true"` promises assistive tech the background is unavailable, which is only
    // true with a portal AND a really `inert` background — and making #root inert would take
    // the z-[110] platform alert banner hostage. An honest non-modal dialog beats a modal that
    // lies (core/components/SearchOverlay.tsx). Do not "fix" this.
    render(modal());
    const panel = panelEl();
    expect(panel.getAttribute('role')).toBe('dialog');
    expect(panel.hasAttribute('aria-modal')).toBe(false);
    const labelledBy = panel.getAttribute('aria-labelledby');
    expect(labelledBy).toBeTruthy();
    expect(document.getElementById(labelledBy as string)?.textContent).toBe('Acme Corp');
  });

  it('sends the first Shift+Tab from the dialog ROOT to the last control instead of escaping', () => {
    // Focus lands on the root on open, and the root is tabindex=-1 so it is not in the
    // focusables list. Without the index === -1 branch this walks straight out of the modal.
    render(modal({ headerActions: <button type="button">Save</button> }));
    const panel = panelEl();
    expect(document.activeElement).toBe(panel);

    // jsdom lays nothing out, so getClientRects() is empty for EVERYTHING and the visibility
    // filter would legitimately find no focusables. Present every control as rendered.
    const spy = allControlsRendered();
    try {
      const event = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true });
      act(() => { panel.dispatchEvent(event); });
      // The × is the last rendered control in the header.
      expect(document.activeElement).toBe(document.querySelector('[aria-label="Close"]'));
    } finally {
      spy.mockRestore();
    }
  });

  it('wraps a forward Tab from the last control back to the first', () => {
    // The mirror of the Shift+Tab case above. Pinned because mutation testing showed the
    // forward branch could be deleted outright with the whole suite still green.
    render(modal({ headerActions: <button type="button">Save</button> }));
    const panel = panelEl();
    const spy = allControlsRendered();
    try {
      const close = document.querySelector('[aria-label="Close"]') as HTMLElement;
      const back = document.querySelector('[aria-label="Back"]') as HTMLElement;
      close.focus();
      const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
      act(() => { panel.dispatchEvent(event); });
      expect(document.activeElement).toBe(back);
    } finally {
      spy.mockRestore();
    }
  });

  it('recovers the trap when focus falls to <body> — a focused control unmounting', () => {
    // The trap is a React onKeyDown on the panel, so it only fires while the event target is a
    // DESCENDANT. Once activeElement is <body> the target is an ANCESTOR and the trap silently
    // stops existing. Reachable today: kanban CardDetailPanel's mode ternary unmounts the
    // focused Cancel button.
    function Harness() {
      const [showButton, setShowButton] = useState(true);
      return (
        <DetailModal title="Card" onClose={() => {}}>
          {showButton && <button type="button" onClick={() => setShowButton(false)}>Cancel</button>}
        </DetailModal>
      );
    }
    render(<Harness />);
    const cancel = Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Cancel') as HTMLElement;
    cancel.focus();
    expect(document.activeElement).toBe(cancel);

    // Unmount it the way the real consumer does, and simulate the browser dropping focus.
    act(() => { cancel.click(); });
    act(() => { (document.body as HTMLElement).focus?.(); document.body.setAttribute('tabindex', '-1'); });
    // Whatever the environment does, the recovery effect must have pulled focus back into the
    // modal rather than leaving it on <body>.
    expect(document.activeElement).toBe(panelEl());
  });

  it('keeps focus on the dialog root when it genuinely contains no rendered control', () => {
    // The real fallback: with nothing focusable, Tab must not escape the modal.
    render(modal());
    const panel = panelEl();
    const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    act(() => { panel.dispatchEvent(event); });
    expect(document.activeElement).toBe(panel);
    expect(event.defaultPrevented).toBe(true);
  });

  it('only considers RENDERED controls, so the variant-hidden chrome cannot swallow a wrap', () => {
    // This trap is the first in the repo whose own chrome is variant-hidden (Back is
    // `dock:hidden`, × is `hidden dock:flex`). querySelectorAll matches regardless of
    // `display`, and .focus() on a display:none element is a silent no-op AFTER
    // preventDefault() has fired — so without the filter, Shift+Tab from the first control
    // does nothing at all on desktop. jsdom does no layout, so we prove the filter is
    // consulted by hiding an element from getClientRects.
    render(modal({ headerActions: <button type="button">Save</button> }));
    const panel = panelEl();
    const back = document.querySelector('[aria-label="Back"]') as HTMLElement;
    const close = document.querySelector('[aria-label="Close"]') as HTMLElement;

    // Simulate the desktop rendering: Back is display:none, × is visible.
    const rects = (el: Element) => (el === back ? [] : [{ width: 10, height: 10 }]);
    const spy = vi.spyOn(Element.prototype, 'getClientRects')
      .mockImplementation(function (this: Element) { return rects(this) as unknown as DOMRectList; });
    try {
      const save = document.querySelector('button[type="button"]') as HTMLElement;
      save.focus();
      // Shift+Tab from the FIRST rendered control must wrap to the last, not to the hidden Back.
      const event = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true });
      act(() => { panel.dispatchEvent(event); });
      expect(document.activeElement).toBe(close);
      expect(document.activeElement).not.toBe(back);
    } finally {
      spy.mockRestore();
    }
  });
});
