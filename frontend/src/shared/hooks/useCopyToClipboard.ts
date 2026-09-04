/**
 * Copy text to the clipboard, with a fallback for non-secure contexts.
 *
 * Ported from the blueprints' `useCopyToClipboard` (cake_os `core/hooks/`, chatty
 * `shared/`) and given the one thing neither of them needs: a real fallback.
 * Upstream is content to `console.warn` on failure because "CAKE is HTTPS in
 * production and localhost in dev, both secure contexts, so the reject is a
 * dev-only edge". CakeCRM breaks that premise on purpose — `/todo/{token}` (#70)
 * is a no-login surface built to be opened from a phone on the LAN, which means
 * plain http, which means `navigator.clipboard` is UNDEFINED. Without the
 * fallback the todo app's Copy buttons would be dead controls on exactly the
 * deployment they exist for.
 */
import { useState, useCallback, useRef, useEffect } from 'react';

/**
 * The legacy path: a hidden textarea plus `document.execCommand('copy')`.
 * Deprecated, and still the only clipboard write available over plain http.
 *
 * `readonly` + an off-screen position rather than `display:none` or
 * `visibility:hidden`: the selection APIs ignore a box that is not rendered, and
 * a focused editable would pop the software keyboard on the phone this exists
 * for. Restoring focus afterwards keeps the edit sheet's field from losing the
 * caret to a copy.
 */
function legacyCopy(text: string): boolean {
  const previous = document.activeElement as HTMLElement | null;
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.position = 'fixed';
  area.style.top = '-9999px';
  area.style.opacity = '0';
  document.body.appendChild(area);
  try {
    area.select();
    area.setSelectionRange(0, text.length);
    return document.execCommand('copy');
  } catch (err) {
    // Logged for the same reason the async path is: this is the path the LAN
    // install actually runs, so a browser that has finally dropped execCommand
    // must leave something behind to diagnose rather than a button that does
    // nothing.
    console.warn('Legacy clipboard copy failed', err);
    return false;
  } finally {
    area.remove();
    previous?.focus?.();
  }
}

/**
 * Write `text` to the clipboard; resolves whether it landed.
 *
 * The async API is probed with a SYNCHRONOUS optional chain rather than a
 * try/await, and that ordering is load-bearing: in a non-secure context
 * `navigator.clipboard` is simply absent, so the check falls through to
 * `legacyCopy` in the same tick as the click that triggered it, while the user
 * gesture `execCommand` requires is still live. Awaiting a rejection first would
 * spend the gesture on some browsers and lose the very case this exists for.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (err) {
      // Present but refused — a denied permission, or a document that lost focus
      // mid-write. Worth one more try on the legacy path even though the gesture
      // may already be spent; logged so it stays diagnosable either way.
      console.warn('Clipboard write failed, trying the legacy path', err);
    }
  }
  return legacyCopy(text);
}

/** What the affordance should be saying right now. */
export type CopyStatus = 'idle' | 'copied' | 'failed';

/**
 * `copyToClipboard` plus the transient status a button renders.
 *
 * ONE status rather than a `copied` flag beside a `failed` flag: the two are
 * mutually exclusive and share a reset timer, and a pair of booleans can hold
 * the impossible state where both are true.
 *
 * Every attempt resets the status before it starts. Without that, a copy that
 * succeeded and a retry 1.5s later that FAILED leave the button still wearing
 * the first attempt's green "Copied" — the user reads it as confirmation of the
 * attempt that just failed, which is worse than no feedback at all.
 */
export function useCopyToClipboard(resetMs = 1500) {
  const [status, setStatus] = useState<CopyStatus>('idle');
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const mountedRef = useRef(false);
  // Only the newest attempt may write the status. Two copies can be in flight at
  // once (a slow rejecting write falls back while a second click resolves), and
  // resolution order is not click order.
  const attemptRef = useRef(0);

  const copy = useCallback(async (text: string) => {
    const attempt = ++attemptRef.current;
    if (timerRef.current) clearTimeout(timerRef.current);
    setStatus('idle');

    const ok = await copyToClipboard(text);

    // The write is asynchronous, so the sheet may already be gone by the time it
    // resolves — tap Copy and close, or clear the title and unmount the button.
    // Clearing the timer on unmount is not enough on its own: without this check
    // the resolving promise starts a NEW one, after the cleanup that was meant
    // to end them.
    if (!mountedRef.current || attempt !== attemptRef.current) return ok;

    setStatus(ok ? 'copied' : 'failed');
    timerRef.current = setTimeout(() => setStatus('idle'), resetMs);
    return ok;
  }, [resetMs]);

  // The reset timer outlives the component otherwise: copy, then close the edit
  // sheet inside `resetMs` and the timeout fires against an unmounted one. The
  // flag is set in the effect rather than at ref init so a StrictMode remount
  // (mount, cleanup, mount) leaves it true rather than stuck false.
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  return { status, copy };
}
