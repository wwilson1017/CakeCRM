import { Component, createRef, type ErrorInfo, type ReactNode } from 'react';

/** Same `cakecrm_` prefix as `cakecrm_theme` / `cakecrm_token`. sessionStorage, not
 *  localStorage: it survives a reload of THIS tab (which is what "one attempt per tab
 *  session" needs) and dies with the tab, so a genuinely new visit gets a fresh attempt. */
export const RELOAD_GUARD_KEY = 'cakecrm_chunk_reload';

/**
 * Does this error look like a chunk that failed to LOAD, rather than a component that threw
 * while rendering? Only the former is fixed by reloading.
 *
 * Browsers word this differently and none of them give it a code, so message matching is the
 * only signal available: Chrome/Edge "Failed to fetch dynamically imported module", Firefox
 * "error loading dynamically imported module", Safari "Importing a module script failed",
 * plus Vite's own "Unable to preload CSS" for a chunk-scoped stylesheet. Matching
 * conservatively is the safe direction — an unrecognised chunk error costs one manual click,
 * while treating a render bug as deploy skew would reload the page out from under the person
 * trying to report it.
 */
function isChunkLoadError(error: unknown): boolean {
  return /dynamically imported module|Importing a module script failed|error loading dynamically|Unable to preload CSS/i
    .test(errorMessage(error));
}

/**
 * The message text to match on, for a value React handed us that is only CONVENTIONALLY an
 * `Error`.
 *
 * A promise can reject with anything, and `String({message: '…'})` is `"[object Object]"` —
 * which matches no pattern, so a genuine chunk failure arriving as a plain object would be
 * classified as an ordinary render bug: no auto-recovery, and a card telling the user this is a
 * bug rather than a deploy. Reading `.message` off a non-Error object costs nothing and cannot
 * misfire in the other direction, since a real render bug throws a real `Error` and takes the
 * first branch.
 */
function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === 'object' && error !== null && 'message' in error) {
    return String((error as { message: unknown }).message ?? '');
  }
  return String(error ?? '');
}

type Props = {
  children: ReactNode;
  /**
   * How much of the app this boundary speaks for.
   *
   * `'app'` (default) is the one in `Root`: the tree below it IS the application, so a full-page
   * card and a one-shot auto-reload are proportionate.
   *
   * `'panel'` wraps a NON-ESSENTIAL subtree — today the assistant drawer — and is the reason
   * this prop exists rather than a second component. `AssistantPanelBody` mounts as soon as
   * `aiReady` is true, drawer closed, in the background. A `Suspense` does not catch a REJECTED
   * import, only a pending one, so without a boundary of its own a chunk that 404s after a
   * deploy propagates to `Root` and replaces the entire CRM — including a half-typed deal form —
   * over a panel the user was not even looking at. A panel boundary contains that to the panel
   * and **never auto-reloads**: reloading to recover a background drawer would destroy exactly
   * the work this containment is protecting. The user gets a compact message and decides.
   */
  scope?: 'app' | 'panel';
};
type State = { failed: boolean; chunk: boolean };

/**
 * Renders a recoverable fallback instead of a blank page when the tree below it throws, and
 * auto-recovers from the one cause that is both common and fixable: deploy skew (#149).
 *
 * Code-splitting introduced a failure mode the single eager bundle did not have. Every
 * deploy replaces `frontend/dist` wholesale inside the Docker image (no CDN, no retained
 * old assets), so a tab left open across a deploy holds an old index whose chunk hashes no
 * longer exist. Before the split every route was resident and no navigation could fail; now
 * the first visit to an unvisited route fetches, gets a hard 404, and the `lazy()` promise
 * rejects. With no boundary that unmounts the tree to a blank page.
 *
 * TWO SEPARATE SCOPES, and conflating them is the mistake to avoid:
 *
 * 1. **What it CATCHES is everything below it.** React error boundaries discriminate by
 *    ancestry, not by error kind, so mounted in `Root` this catches any render error anywhere.
 *    That is deliberate — a card with a Reload button beats the blank page this app produced
 *    until now — but it does mean this is the app's FIRST general error boundary. It is NOT a
 *    licence to stop handling errors locally: a component that can fail in a way the user
 *    should understand still owes them a specific message.
 * 2. **What it AUTO-RELOADS on is only a chunk-load failure.** Reloading is the remedy for
 *    deploy skew and for nothing else; on a render bug it would just replay the crash and
 *    hide it. Hence `isChunkLoadError`.
 *
 * A boundary rather than a global `vite:preloadError` listener, so that any future dynamic
 * import with its own retry UI keeps ownership of its failure. Not calling `preventDefault()`
 * on Vite's preload error is correct rather than an omission: Vite's rethrow is exactly what
 * makes the `lazy()` promise reject so this can catch it.
 */
export default class ChunkErrorBoundary extends Component<Props, State> {
  state: State = { failed: false, chunk: false };

  /** The card must not assert a cause it cannot know, so the KIND is captured here. */
  static getDerivedStateFromError(error: Error): State {
    return { failed: true, chunk: isChunkLoadError(error) };
  }

  private readonly alertRef = createRef<HTMLDivElement>();

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Render failed below ChunkErrorBoundary', error, info.componentStack);
    if (this.props.scope === 'panel') return; // contained: never reload the page for a panel
    if (isChunkLoadError(error) && this.shouldAutoReload() && this.claimReloadAttempt()) {
      window.location.reload();
    }
  }

  /**
   * Deploy skew is the case reloading FIXES; the browser's wording does not distinguish it from
   * the cases reloading makes worse.
   *
   * The same four messages cover an offline transition, a captive portal, a proxy fault and a
   * transient network blip. Reloading while offline is actively harmful: it throws away a page
   * the user can still read — and any text they were typing into it — and replaces it with the
   * browser's own offline error, from which the app cannot recover itself. A known-offline
   * browser is the one signal available for free, so it is the one case handled; the residual
   * (a proxy that fails while `onLine` is true) still auto-reloads once, which is the accepted
   * cost of recovering the common case without a network probe.
   *
   * `onLine === false` is trustworthy in the direction used here — browsers report it only when
   * there is genuinely no connection — while `true` famously means "an interface is up", which
   * is why the test is for false rather than a requirement of true.
   */
  private shouldAutoReload(): boolean {
    return typeof navigator === 'undefined' || navigator.onLine !== false;
  }

  // The subtree that held focus was just destroyed, so focus is stranded on nothing. Move it
  // to the card, or a keyboard/screen-reader user has no route to the only control on the
  // page. BOTH lifecycles are needed: a throw during the initial render commits the fallback
  // as this boundary's first mount (componentDidMount), while a child that throws later is an
  // update — covering only the latter is a focus move that silently never happens on a cold
  // boot, which is the commonest case of all.
  componentDidMount() {
    if (this.state.failed) this.alertRef.current?.focus();
  }

  componentDidUpdate(_prev: Props, prevState: State) {
    if (!prevState.failed && this.state.failed) this.alertRef.current?.focus();
  }

  /**
   * True at most ONCE per tab session, and only when the attempt could actually be recorded.
   *
   * A time WINDOW was the obvious shape and is wrong: a chunk that is genuinely gone (offline,
   * or an asset that never deploys) fails again after every reload, so any expiring guard
   * turns into a permanent reload cycle. One attempt per session cannot cycle. The cost is
   * that a tab living through a SECOND, unrelated deploy skew shows the Reload button instead
   * of recovering by itself — acceptable, since `sessionStorage` dies with the tab.
   *
   * Writing then reading BACK is the other half: when storage is blocked the write is silently
   * lost, and a guard that cannot be stored cannot stop a loop — so unavailable storage means
   * "do not auto-reload" and the user gets the button.
   */
  private claimReloadAttempt(): boolean {
    try {
      if (sessionStorage.getItem(RELOAD_GUARD_KEY) !== null) return false;
      sessionStorage.setItem(RELOAD_GUARD_KEY, String(Date.now()));
      return sessionStorage.getItem(RELOAD_GUARD_KEY) !== null;
    } catch {
      // Storage blocked (private mode, locked-down device) — the manual button is the only
      // recovery we can offer without risking a cycle.
      return false;
    }
  }

  render() {
    if (!this.state.failed) return this.props.children;

    // A panel says its piece inside its own box and leaves the page alone. No auto-reload has
    // run (see componentDidCatch), so the Reload button is the ONLY reload here and the user
    // chooses it knowing what they have open.
    if (this.props.scope === 'panel') {
      const { chunk } = this.state;
      return (
        <div
          ref={this.alertRef}
          role="alert"
          tabIndex={-1}
          className="flex flex-col items-center justify-center gap-3 p-6 text-center text-ck-ink-mute"
        >
          <p className="m-0">
            {chunk
              ? 'Couldn’t load this panel. It may have been updated while your tab was open.'
              : 'Something went wrong in this panel.'}
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="bg-ck-accent hover:bg-ck-accent-dark text-ck-accent-ink font-display px-4 py-1.5 rounded cursor-pointer border-0"
          >
            Reload the page
          </button>
        </div>
      );
    }
    // A visible retry, not a white screen. Only a chunk failure is explained by a deploy:
    // telling someone hitting a deterministic render bug that "the app was updated" is false
    // AND self-defeating — they reload, hit the same crash, and stop reporting it.
    const { chunk } = this.state;
    return (
      <div
        ref={this.alertRef}
        role="alert"
        tabIndex={-1}
        className="min-h-svh bg-ck-bg flex flex-col items-center justify-center gap-4 p-6 text-center"
      >
        <h1 className="text-ck-ink font-display text-lg m-0">
          {chunk ? 'Couldn’t load this page.' : 'Something went wrong on this page.'}
        </h1>
        <p className="text-ck-ink-mute max-w-sm m-0">
          {chunk
            ? 'This usually means CakeCRM was updated while your tab was open. Reloading gets the latest version.'
            : 'Reloading may help. If it keeps happening, report it — this one is a bug, not an update.'}
        </p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="bg-ck-accent hover:bg-ck-accent-dark text-ck-accent-ink font-display px-5 py-2 rounded cursor-pointer border-0"
        >
          Reload
        </button>
      </div>
    );
  }
}
