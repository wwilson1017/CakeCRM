import { useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react';
import { IconArrowLeft, IconX } from '../icons';
import { acquireBodyScrollLock } from '../hooks/useBodyScrollLock';

/**
 * The ONE detail overlay for the whole app.
 *
 * A full-screen takeover when the viewport is small or short, and a **centred modal** when
 * there is room — the takeover/centre decision is the `dock:` variant from `index.css`
 * (`min-width:768px and min-height:600px`) and nothing else. There is deliberately no JS
 * viewport branching anywhere in this file: `useIsMobile` is width-only, and width alone is
 * exactly what put a landscape phone on the desktop branch in the blueprint.
 *
 * In the blueprint four near-identical panel shells each half-implemented this before it was
 * centralised here; they became thin adapters that delegate to this modal and keep owning their
 * own app-local `Section`/`InfoRow`/`LinkRow` content chrome, so no call site had to change.
 *
 * **Why a centred modal replaced the right-hand dock.** The dock had to duck under the header
 * (`dock:z-40`) so the theme toggle in `UserMenu` stayed reachable while the board beside it
 * stayed usable — and that left the panel's × geometrically underneath an open header
 * dropdown, where the dropdown's own outside-`mousedown` guard swallowed the click. A modal
 * removes that collision by construction: the mousedown that opens a card is outside
 * `menuRef` and dismisses any open dropdown before this even mounts. Covering the header
 * while open is fine here in a way it never was for the dock: a dock is a persistent work
 * state, a modal is a blocking interaction that × or Escape ends in one gesture.
 *
 * **Scroll container.** `overflow-y-auto` lives on the panel element itself, NOT on an inner
 * body div, and the header is `sticky top-0` inside it. That is load-bearing, not styling:
 * a sibling surface's guided close-out bar is a `sticky bottom-0` LAST CHILD of the panel body
 * (the blueprint's request detail panel), deliberately not a modal of its own so it cannot fork `draft`
 * state or double-mount the signature canvas. Move the scrollport and that bar stops
 * riding the bottom edge.
 *
 * **Stacking, and the assistant launcher (`underLauncher`).** The CRM floats one persistent
 * launcher button (`crm/components/AssistantLauncher.tsx`, `zIndex: 40`) on every page, and it is
 * the only control that must stay reachable OVER an open record detail: it opens the assistant
 * drawer carrying that record's context (#14), which is the one moment such context exists. So a
 * caller may pass `underLauncher` to render the CENTRED modal at `dock:z-[39]`, beneath the
 * button, while the full-screen takeover stays at `z-50` above it — a takeover cannot share the
 * corner with a floating button, and on a phone a poked-through launcher would sit on the panel's
 * own bottom controls and steal taps. The drawer itself (z-59, scrim 58), `ConfirmHost`
 * (z-150) and the toast viewport (z-200) stay above both branches either way.
 *
 * It is a PROP and not the default because "beneath the launcher" is a fact about record
 * details, not about every overlay: a form modal or a confirm SHOULD occlude the button. It is
 * also not a per-surface opt-in in practice — `CollectionDetail` passes it unconditionally, so
 * every collection detail inherits the rule with nothing to remember.
 *
 * The Tab trap therefore admits ONE element outside the panel in this mode — whatever carries
 * `data-detail-companion` — so the launcher is reachable by keyboard and not only by pointer.
 * Only in the CENTRED layout, and only while that element is genuinely uncovered: the takeover
 * renders at `z-50` and covers the launcher, so the hand-off is gated on the `dock:` probe below;
 * and `AssistantLauncher` withholds the attribute (and drops beneath this panel) whenever it
 * would NAVIGATE rather than open the drawer, since a navigation unmounts the panel and takes
 * any draft with it without ever reaching this modal's close guard.
 * Residual, disclosed: once the drawer itself closes and returns focus to that button, a Tab
 * pressed while focus sits outside BOTH surfaces still walks the covered page. That is not a
 * regression (the hand-rolled sheet this replaced had no trap at all), but a full launcher /
 * detail / drawer focus contract is its own piece of work.
 *
 * **`role="dialog"` WITHOUT `aria-modal`, on purpose.** `aria-modal="true"` promises assistive
 * tech that everything outside is unavailable, which is only true with a portal AND a really
 * `inert` background — and making `#root` inert would take the platform alert banner
 * (`z-[110]`, a high-priority alert surface) and the compliance overlays hostage for as long
 * as any detail is open. `core/components/SearchOverlay.tsx` states the rule: an honest
 * non-modal dialog beats a modal that lies. Do not "fix" this into a lie.
 *
 * **No portal, also on purpose.** `CrmPage` keeps inactive tabs mounted at `display:none`; a
 * portaled modal opened from a hidden tab would escape the hidden subtree and cover the active
 * tab, whereas an in-place `fixed` element disappears with its ancestor. Every shell this
 * replaces was already a non-portaled `fixed` element, so no consumer has a transformed
 * ancestor (which would mis-anchor `fixed`) — verified across all nine surfaces.
 */
interface DetailModalProps {
  title: string;
  subtitle?: string;
  /**
   * `source` says WHICH chrome closed: `'escape'` for the document Escape handler,
   * `'button'` for the Back arrow and the ×. Optional and ignorable — the four app adapters
   * pass zero-arg handlers — but `shared/collection`'s CollectionDetail routes every leave
   * path through one `onRequestClose(reason)` contract and needs the distinction without
   * re-implementing the Escape stack logic above.
   */
  onClose: (source?: 'escape' | 'button') => void;
  /** Rendered in the sticky header, beside the desktop ×. */
  headerActions?: ReactNode;
  /**
   * What a backdrop click does. Default: **nothing**. The backdrop is a click shield first and
   * a dimming artefact second — most of these surfaces carry an edit form, and dismissing one
   * on a stray tap is the data-loss vector `CardOverlayShell` was already written to prevent.
   * Pass `onClose` to opt a read-only surface in.
   */
  onBackdropClick?: () => void;
  /**
   * Whether the document-level Escape handler closes this modal. Default true. Adapters whose
   * surfaces have no dirty-close confirm pass `false` rather than shipping silent data loss.
   */
  closeOnEscape?: boolean;
  /**
   * Stack the CENTRED modal beneath the assistant launcher. Default `false` (plain `z-50`).
   *
   * `shared/collection`'s `CollectionDetail` passes it unconditionally and is its only caller —
   * see the "Stacking" note above for why the rule belongs to record details rather than to
   * every overlay.
   */
  underLauncher?: boolean;
  children: ReactNode;
}

/**
 * Open modals, oldest first — holds each dialog's ELEMENT, not just an identity, so a closing
 * modal can hand focus to the one below it directly (mutating this array re-renders nobody, so
 * the survivor's own effects will not run). Modelled on `shared/media/ImageLightbox.tsx`.
 *
 * This stack is scoped to DetailModals. `ImageLightbox` keeps its own, and the two are
 * deliberately independent — which is precisely why nothing in this file may assume that being
 * topmost *here* means being topmost on screen. See the focus and Tab notes below.
 */
const openStack: Array<{ id: symbol; el: HTMLElement | null }> = [];

/**
 * The modal that should answer a keypress: the topmost entry that is actually on screen.
 *
 * With membership keyed on visibility this is normally just the last entry — but it walks down
 * past unrendered ones for the same reason `focusSurvivor` does, and that reason is worth
 * stating: membership is dropped when a hidden modal RE-RENDERS and re-measures itself. That
 * holds today because `CrmPage` flips a `hidden` class on a parent, which re-renders every tab
 * child in the same commit. Wrap a tab in `React.memo` — an obvious performance move — and the
 * hidden modal never re-renders, never leaves the stack, and starts answering Escape where
 * nobody can see it. This walk makes that a latent inefficiency rather than a live bug.
 */
const topRenderedId = (): symbol | undefined => {
  for (let i = openStack.length - 1; i >= 0; i--) {
    if (isRendered(openStack[i].el)) return openStack[i].id;
  }
  return undefined;
};

/**
 * Is this element actually on screen?
 *
 * Deliberately the same measurement the visibility effect below uses to decide stack
 * membership — one definition of "rendered" for the whole file, so a modal cannot be in the
 * stack by one rule and skipped by another. (`visibleFocusables` uses `getClientRects()`
 * instead because it asks a different question: whether one CONTROL is display:none inside an
 * otherwise-visible panel.)
 */
function isRendered(el: HTMLElement | null): el is HTMLElement {
  return !!el && el.isConnected && el.getBoundingClientRect().width > 0;
}

function dropFromStack(id: symbol) {
  const i = openStack.findIndex(entry => entry.id === id);
  if (i !== -1) openStack.splice(i, 1);
}

/**
 * Hand focus to the topmost modal still on screen, or back to the trigger if none is.
 *
 * Handing focus back to OUR trigger while another modal still covers the screen would park it
 * behind that dialog, outside its Tab trap — and the survivor cannot fix that itself, because
 * splicing the module-level stack re-renders nobody.
 *
 * It walks DOWN the stack past any entry that is not rendered, rather than trusting the top.
 * Stack membership is already visibility-keyed, so that should be redundant — it is kept as a
 * cheap belt-and-braces because `.focus()` on a `display:none` element fails SILENTLY, dropping
 * focus to `<body>`, which is precisely the failure this function exists to prevent.
 */
function focusSurvivor(trigger: HTMLElement | null) {
  for (let i = openStack.length - 1; i >= 0; i--) {
    const el = openStack[i].el;
    if (isRendered(el)) {
      el.focus();
      return;
    }
  }
  // The trigger gets the SAME rendered-ness test, not just `isConnected`: it can itself be
  // inside a tab that has since been hidden, and focusing a `display:none` element fails
  // silently — dropping focus to `<body>`, the exact outcome this function exists to avoid.
  if (isRendered(trigger) && typeof trigger.focus === 'function') trigger.focus();
}

/**
 * Focusable controls, restricted to ones that are actually RENDERED.
 *
 * The visibility filter is required here in a way it is not for the repo's other traps: this
 * component's own chrome is variant-hidden — the Back arrow is `dock:hidden` and the × is
 * `hidden dock:flex` — so on desktop the Back button is still `focusables[0]` and on mobile
 * the × is still the last entry, while `.focus()` on a `display:none` element is a silent
 * no-op *after* the handler has already called `preventDefault()`. Without the filter,
 * Shift+Tab from the first control does nothing at all on desktop, forever.
 *
 * jsdom does no layout and evaluates no media queries, so a wrap test cannot catch that
 * regression on its own — `DetailModal.test.tsx` stubs `getClientRects` to prove this filter
 * is consulted.
 */
const FOCUSABLE_SELECTOR = [
  'button:not([disabled])',
  '[href]',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

function visibleFocusables(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
    .filter(el => el.getClientRects().length > 0);
}

/**
 * The ONE control an `underLauncher` modal deliberately does not cover, marked so the Tab trap
 * can let keyboard users reach it.
 *
 * Rendering the launcher above the panel makes it reachable by POINTER; without this the trap
 * still cycles strictly among the panel's own descendants, so a keyboard-only user could not
 * reach it at all — worse than the untrapped sheet this replaced, and it would hollow out the
 * whole point of the mode. An attribute rather than a ref keeps the shared file free of any
 * knowledge of which app control it is.
 */
const COMPANION_SELECTOR = '[data-detail-companion]';

function visibleCompanion(root: HTMLElement): HTMLElement | null {
  for (const el of document.querySelectorAll<HTMLElement>(COMPANION_SELECTOR)) {
    if (!root.contains(el) && el.getClientRects().length > 0) return el;
  }
  return null;
}

export default function DetailModal({
  title,
  subtitle,
  onClose,
  headerActions,
  onBackdropClick,
  closeOnEscape = true,
  underLauncher = false,
  children,
}: DetailModalProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  // Is the CENTRED layout live rather than the full-screen takeover? Answered by a probe element
  // carrying the `dock:` variant ITSELF, never by a media query in JS: this file's whole premise
  // is that the takeover/centre decision is `dock:` and nothing else, and a JS copy of that query
  // is a second definition that drifts. `useIsMobile` is width-only, which is exactly what put a
  // landscape phone on the wrong branch in the blueprint.
  const dockProbeRef = useRef<HTMLSpanElement>(null);
  const titleId = useId();
  // Whether the mousedown / mouseup halves of the current gesture each landed on the backdrop
  // itself — BOTH are tracked because the click event alone can prove neither; see the
  // wrapper's handlers below.
  const pressedBackdropRef = useRef(false);
  const releasedOnBackdropRef = useRef(false);

  // Stable per-instance identity, shared by the mount effect and the Escape handler. The lazy
  // `useState` initializer runs exactly once — `useRef(Symbol(…))` would mint a throwaway Symbol
  // on every render, and assigning one during render reads/writes a ref mid-render.
  const [instanceId] = useState(() => Symbol('detail-modal'));

  // Read through refs so an inline `onClose` arrow cannot tear down and re-register the
  // document listener on every render. Synced in an unconditional effect rather than during
  // render: the effect runs after commit and before any user event can reach the handler, so the
  // listener never sees a stale value.
  const closeRef = useRef(onClose);
  const closeOnEscapeRef = useRef(closeOnEscape);
  useEffect(() => {
    closeRef.current = onClose;
    closeOnEscapeRef.current = closeOnEscape;
  });

  // ---- Visibility drives everything: scroll lock, stack membership, focus -------------------
  //
  // MOUNTED IS NOT THE SAME AS ON SCREEN, and conflating the two is a real bug rather than a
  // theoretical one. `CrmPage` renders every tab and hides the inactive ones with `hidden`
  // (`display:none`), so a detail modal stays MOUNTED when its tab is switched away. And CRM
  // navigates *between* entities exactly that way: the Company link inside a contact detail
  // calls `CrmContext.navigateToCompany`, which only flips `activeTab` — so the contact modal
  // is left mounted-but-invisible while a company modal opens on another tab.
  //
  // Keying the stack on MOUNT therefore left invisible modals in it, with three consequences:
  // closing the visible modal handed focus to a `display:none` element (a silent no-op, so
  // focus fell to `<body>`); a hidden modal could sit topmost and answer Escape while the user
  // looked at a different one; and the focus-recovery effect below, which stands down unless it
  // is topmost, would stay disarmed on the modal that actually needs it. Keying on measured
  // VISIBILITY fixes all three at once, and it re-focuses a modal that becomes visible again —
  // a mount-keyed effect cannot, because it does not re-run.
  //
  // One effect owns all three concerns deliberately: they share a trigger and must be ordered
  // against each other (the lock is released before focus moves, because focusing an element
  // can scroll it into view and that needs the page in its final locked/unlocked state).
  //
  // No dependency array: visibility is a post-layout fact, so it has to be re-measured every
  // render rather than derived from props. The work is gated on a CHANGE, so the steady state
  // is one `getBoundingClientRect()` per render and nothing else — in particular the scroll
  // lock is not dropped and retaken, which would re-run the shared module's layout-forcing
  // measurement on every keystroke in an open panel.
  const releaseScrollLock = useRef<(() => void) | null>(null);
  const visibleRef = useRef(false);
  const triggerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const panel = panelRef.current;
    const visible = !!panel && panel.getBoundingClientRect().width > 0;
    if (visible === visibleRef.current) return;
    visibleRef.current = visible;

    if (visible) {
      triggerRef.current = document.activeElement as HTMLElement | null;
      openStack.push({ id: instanceId, el: panel });
      // Lock BEFORE focusing, mirroring the hide branch's release-then-move. Focusing an
      // element can scroll it into view, which wants the page already in its final state.
      // (Harmless either way for this panel — it is `fixed`, so there is nothing to scroll —
      // but an earlier revision had the two branches ordered inconsistently while the comment
      // above claimed otherwise, and a comment that disagrees with its code is a trap.)
      releaseScrollLock.current = acquireBodyScrollLock();
      panel?.focus();
    } else {
      dropFromStack(instanceId);
      releaseScrollLock.current?.();
      releaseScrollLock.current = null;
      focusSurvivor(triggerRef.current);
      triggerRef.current = null;
    }
  });

  // Unmount is the one transition the render-time effect above cannot see: a modal closed while
  // still visible never re-renders to observe itself becoming hidden.
  useEffect(() => () => {
    if (!visibleRef.current) return;
    visibleRef.current = false;
    dropFromStack(instanceId);
    releaseScrollLock.current?.();
    releaseScrollLock.current = null;
    focusSurvivor(triggerRef.current);
    triggerRef.current = null;
  }, [instanceId]);

  // Escape — BUBBLE phase, and that is the whole contract with `ImageLightbox`.
  //
  // The lightbox takes Escape on the document in the CAPTURE phase and calls
  // `preventDefault()`; capture always runs before bubble, so when a lightbox is open over
  // this modal one Escape closes only the lightbox and the next closes the modal. Registering
  // this in capture too would make it a registration-order race and close both. The same
  // `defaultPrevented` deference is what lets the camera scanner and any future nested overlay
  // consume the key for themselves.
  useEffect(() => {
    const onKeyDown = (e: globalThis.KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      if (!closeOnEscapeRef.current) return;
      // Listeners fire in registration order (oldest first), so without this the modal
      // UNDERNEATH would consume Escape and close, stranding the one on top.
      if (topRenderedId() !== instanceId) return;
      closeRef.current('escape');
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [instanceId]);

  // Recover the trap when focus falls to `<body>`.
  //
  // The Tab trap below is a React `onKeyDown` on the panel, so it only fires while the native
  // event's target is a DESCENDANT of the panel. When `document.activeElement` becomes `<body>`
  // the target is an ANCESTOR, React never dispatches to the handler, and native Tab order
  // takes over — walking straight into the background content the backdrop only *covers*. That
  // matters more here than in most modals: this component deliberately does not claim
  // `aria-modal`, so the trap IS the compensating control.
  //
  // Focus lands on `<body>` whenever the focused element stops being focusable, and there is a
  // reachable path in the app today: `the blueprint's card detail panel`'s `mode === 'edit'`
  // ternary unmounts the whole `CardForm` — including the Cancel button the user just pressed —
  // when it flips back to view. A sibling surface's `ConfirmModal` documents the identical failure
  // ("the browser blurs the focused (now-disabled) button to <body>") and fixes it the same way.
  //
  // The gate is `activeElement === document.body`, NOT `ImageLightbox`'s
  // `!root.contains(activeElement)`. That distinction is the whole reason this is safe to have:
  // the broader check would fire while a portaled lightbox legitimately holds focus above us —
  // and since this component keeps its own stack, we cannot tell a lightbox is there — so it
  // would fight the lightbox for focus on every one of the blueprint's request detail panel's
  // per-keystroke renders.
  //
  // Be precise about what makes the NARROW check safe, because the obvious claim is false: a
  // lightbox does NOT always hold focus on a descendant of its own root. Its own docstring
  // records the Retry button unmounting under focus, and its zoom buttons carry `disabled` at
  // the scale bounds — both drop `activeElement` to `<body>` while the lightbox is still open.
  // What actually protects us is a two-part invariant: the lightbox runs its own every-render
  // focus recovery, and React runs CHILD effects before parent ones — so in any commit where
  // both re-render, the lightbox reclaims focus before this effect observes anything.
  //
  // That invariant has a named enemy: memoizing the gallery/lightbox subtree. It is a tempting
  // response to exactly the render storm discussed above, and it would stop the lightbox
  // re-rendering in step with this modal — at which point a poll-driven render here could see
  // body-focus and pull focus underneath an open lightbox. Do not memoize that subtree without
  // revisiting this.
  //
  // Runs after every render, like both prior-art copies, because the trigger is conditional
  // content unmounting rather than anything mount-shaped.
  useEffect(() => {
    if (topRenderedId() !== instanceId) return;
    if (document.activeElement === document.body || !document.activeElement) {
      panelRef.current?.focus();
    }
  });

  // Tab trap.
  const onTrapKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'Tab') return;
    // Two guards, and they cover DIFFERENT cases — do not collapse them.
    //
    // `ImageLightbox` portals to `document.body` but is RENDERED from inside this panel's React
    // subtree (a photo gallery), and React synthetic events
    // propagate along the React tree, not the DOM tree — so its Tab presses arrive here.
    // Without a guard, they fall into the `index === -1` branch below and yank focus out of the
    // open lightbox and down into the modal underneath.
    //
    // For THAT overlay both guards happen to hold at once (it is portaled, so focus is outside
    // this panel's DOM, *and* it calls `preventDefault()`), so the end-to-end lightbox test
    // cannot tell you which one is doing the work — an earlier version of this comment claimed
    // the containment check was "the load-bearing one" and that was never demonstrated.
    // `DetailModal.overlays.test.tsx` now isolates each: `defaultPrevented` is what protects a
    // nested overlay that is NOT portaled (focus genuinely inside this panel, key already
    // claimed), and containment is what protects a Tab arriving while focus sits outside the
    // panel without anything having claimed it. A future nested overlay may satisfy only one.
    if (e.defaultPrevented) return;
    const root = panelRef.current;
    if (!root) return;

    if (!root.contains(document.activeElement)) return;

    // In `underLauncher` mode the wrap at either END hands off to the companion instead of
    // cycling, so the one control this mode deliberately leaves uncovered is reachable by
    // keyboard and not only by pointer.
    //
    // Only the OUTBOUND direction is ours to implement: this trap is a React `onKeyDown` on the
    // panel, so it fires only for keys whose target is inside the panel's subtree — a Tab pressed
    // while focus sits ON the companion never reaches this handler at all. Getting back is
    // therefore native order, which is the honest behaviour for a `role="dialog"` that
    // deliberately does not claim `aria-modal`: this panel does not own the whole screen, and
    // says so.
    // The hand-off belongs to the CENTRED layout only. On the takeover this panel renders at
    // `z-50` and COVERS the launcher, so the companion is no longer "the one control this mode
    // leaves uncovered" — it is behind the modal. `getClientRects()` cannot see occlusion, so it
    // would still qualify, and Tab past the last control would focus an invisible button.
    const centred = (dockProbeRef.current?.getClientRects().length ?? 0) > 0;
    const companion = underLauncher && centred ? visibleCompanion(root) : null;

    const focusables = visibleFocusables(root);
    if (focusables.length === 0) {
      e.preventDefault();
      root.focus();
      return;
    }
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    // Focus starts on the dialog ROOT, which is `tabindex="-1"` and so not in `focusables`.
    // Without this branch the very first Shift+Tab matches neither wrap condition and walks
    // straight out of the modal into the page behind it.
    const index = focusables.indexOf(document.activeElement as HTMLElement);
    if (index === -1) {
      e.preventDefault();
      (e.shiftKey ? last : first).focus();
      return;
    }
    if (companion && ((e.shiftKey && document.activeElement === first) || (!e.shiftKey && document.activeElement === last))) {
      e.preventDefault();
      companion.focus();
      return;
    }
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  return (
    // Wrapper: an invisible full-screen container on the takeover, the dimming backdrop once
    // there is room to centre. When `onBackdropClick` is undefined the click simply does
    // nothing — the element still absorbs it, which is the point.
    <div
      // Both class strings are written out in full rather than composed from a shared prefix:
      // Tailwind scans source text for complete class names, and a concatenated `z-50 ${…}`
      // would leave `dock:z-[39]` ungenerated.
      className={
        underLauncher
          ? 'fixed inset-0 z-50 dock:z-[39] dock:flex dock:items-center dock:justify-center dock:bg-black/50 dock:p-4'
          : 'fixed inset-0 z-50 dock:flex dock:items-center dock:justify-center dock:bg-black/50 dock:p-4'
      }
      // A backdrop dismissal must be a CLICK ON THE BACKDROP, not merely a click event that
      // reached it. When mousedown and mouseup land on different elements the browser fires
      // `click` on their nearest common ancestor — and this wrapper is the panel's PARENT, so
      // selecting text inside the panel and releasing over the dim area would dismiss, as
      // would a press on the backdrop dragged onto the panel. The old kanban backdrop was a
      // SIBLING of its panel, so a cross-element drag resolved to their un-handled parent and
      // the problem could not arise; the centred geometry is what introduces it.
      //
      // Both gesture ends are tracked with their OWN ref because the click event can prove
      // neither: a cross-element gesture's click targets the common ancestor — this wrapper —
      // in BOTH directions, so `click.target === currentTarget` is true even when the release
      // landed inside the panel, and the panel's stopPropagation never runs (the click never
      // targets the panel). A backdrop-press released on a panel button used to dismiss AND
      // eat the button's own click; only requiring mousedown-on-wrapper plus
      // mouseup-on-wrapper actually means "both ends of the gesture on the backdrop".
      onMouseDown={e => { pressedBackdropRef.current = e.target === e.currentTarget; }}
      onMouseUp={e => { releasedOnBackdropRef.current = e.target === e.currentTarget; }}
      onClick={e => {
        if (!onBackdropClick) return;
        if (e.target !== e.currentTarget) return;
        if (!pressedBackdropRef.current) return;
        if (!releasedOnBackdropRef.current) return;
        onBackdropClick();
      }}
    >
      {/* Zero-size probe for the `dock:` variant, so the Tab hand-off below can tell the centred
          layout from the takeover without a second copy of that media query in JS. A `<span>`
          outside the panel: not focusable, so the trap's own focusable scan never sees it, and
          `aria-hidden` keeps it out of the accessibility tree. `hidden` / `dock:block` are
          written as complete class names because that is what Tailwind scans for. */}
      {underLauncher && (
        <span ref={dockProbeRef} aria-hidden="true" className="hidden dock:block" />
      )}
      <div
        ref={panelRef}
        role="dialog"
        aria-labelledby={titleId}
        tabIndex={-1}
        // Keeps clicks inside the panel off the wrapper (ConfirmModal's pattern). Note this
        // stops the NATIVE event at React's root container too, so a document-level
        // bubble-phase `click` listener will not fire for clicks in here. MOST click-away hooks
        // in this repo use `mousedown` and are unaffected, but not all — a couple of sibling
        // ID-lookup sheets listen for `click`. Neither renders inside a detail
        // overlay today, so there is no live bug; add a `click`-based click-away inside one and
        // it will silently never fire.
        onClick={e => e.stopPropagation()}
        onKeyDown={onTrapKeyDown}
        // `dock:max-h-[calc(100dvh-2rem)]` rather than `max-h-full`: `dock:` admits an iPad in
        // portrait, and `fixed inset-0` resolves against iOS Safari's LARGE viewport, so a
        // percentage max-height extends under the collapsed toolbar. Same reasoning and same
        // unit as `core/components/SearchOverlay.tsx`. The `2rem` matches the wrapper's `p-4`.
        className="h-full w-full overflow-y-auto overscroll-contain bg-cream shadow-xl focus:outline-none dock:h-auto dock:max-h-[calc(100dvh-2rem)] dock:max-w-2xl dock:rounded-xl dock:border dock:border-line"
      >
        {/* `dock:py-4`, not `md:py-4`: the roomier vertical padding is worth it when there is
            height to spare and costs screen exactly when there isn't. `px-6` stays `md:` —
            horizontal room really is a width question. */}
        <div className="sticky top-0 z-10 flex items-center justify-between gap-2 border-b border-line bg-cream px-4 py-3 md:px-6 dock:py-4">
          <div className="flex min-w-0 flex-1 items-center gap-1">
            {/* Flips WITH the presentation, never independently — on a width-only rule a
                landscape phone got the takeover with only the desktop × to escape it. */}
            <button onClick={() => onClose('button')} className="-ml-2 p-2 text-charcoal dock:hidden" aria-label="Back">
              <IconArrowLeft size={20} />
            </button>
            <div className="min-w-0">
              {/* Wraps rather than truncating: a long title was ellipsis-clipped to one
                  line and simply unreadable in the panel that exists to show it. `break-words`
                  also breaks a pathological unbroken string instead of overflowing. */}
              <h2 id={titleId} className="break-words font-heading text-lg font-semibold text-charcoal">
                {title}
              </h2>
              {subtitle && <p className="truncate text-xs text-muted">{subtitle}</p>}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {headerActions}
            {/* Built like the Back button opposite it — a lucide glyph in symmetric padding,
                with a negative margin pulling it flush to the header's padding edge while the
                tap target extends past it — but deliberately BIGGER: `p-2.5` around a 24px icon
                is a 44px square, the floor-tablet tap target `shared/media/ImageLightbox.tsx`
                documents, where Back is 36px. That applies here because `dock:` admits an iPad
                in portrait, so this is a touch target and not only a mouse one. It rests at
                `text-charcoal` rather than `text-muted` so it reads without hover, which then
                only has to signal hit-ability. */}
            <button
              onClick={() => onClose('button')}
              aria-label="Close"
              className="-mr-2.5 hidden items-center justify-center rounded-md p-2.5 text-charcoal hover:bg-sand dock:flex"
            >
              <IconX size={24} />
            </button>
          </div>
        </div>
        <div className="pb-[env(safe-area-inset-bottom)]">{children}</div>
      </div>
    </div>
  );
}

export type { DetailModalProps };
