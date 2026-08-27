import { useEffect, useRef, useState } from 'react';
import { apiBlob } from '../core/api/client';

interface State {
  /** The path this url/error belongs to. Comparing it to the CURRENT path is what makes
   *  a stale result unusable rather than merely late. */
  path: string | null;
  url: string | null;
  error: boolean;
}

/**
 * Fetch an authenticated binary endpoint and expose it as an object URL (issue #57).
 *
 * This one hook is the entire "never a bare `<img src>`" mechanism. CakeCRM's auth is a
 * Bearer token in sessionStorage with no cookie fallback, so a browser-issued image
 * request would carry no credential at all — the bytes have to be fetched by script and
 * handed to the tag as a blob.
 *
 * It is also where object-URL lifetime is bounded, which is the other half of the issue's
 * "bounded image memory" requirement: an object URL pins its Blob in memory until it is
 * revoked, so a thread of thumbnails that forgot to revoke would hold every image it ever
 * scrolled past.
 *
 * The returned url is **keyed to the current path**. A naive implementation returns the
 * previous path's url while the next one is in flight, and its effect cleanup has already
 * revoked that url — so the consumer renders a revoked URL, which is a broken image with
 * no error event. Here a path change yields `null` until the new bytes arrive.
 */
export function useAuthedBlobUrl(path: string | null): { url: string | null; error: boolean } {
  const [state, setState] = useState<State>({ path: null, url: null, error: false });
  // Bumped per request so a resolution can tell whether it is still the live one. A stale
  // response must not create an object URL at all — creating one and revoking it later is
  // a leak whenever "later" never comes.
  const reqRef = useRef(0);

  useEffect(() => {
    // Bumped even when there is nothing to fetch, so an in-flight request for the
    // PREVIOUS path is invalidated the moment the path clears.
    const reqId = ++reqRef.current;
    // No state reset here, deliberately (and it would violate react-hooks'
    // set-state-in-effect rule): the return below is path-keyed, so a leftover result for
    // the old path already reads as `null` without writing anything.
    if (!path) return;

    let created: string | null = null;
    let cancelled = false;
    // Abandoning the promise stops us USING the bytes; it does not stop them arriving. A
    // closed lightbox would otherwise keep pulling a multi-megabyte original into a Blob
    // that is immediately garbage — and several open/close cycles would run several such
    // downloads at once.
    const controller = new AbortController();

    apiBlob(path, controller.signal)
      .then(blob => {
        if (cancelled || reqId !== reqRef.current) return;
        created = URL.createObjectURL(blob);
        setState({ path, url: created, error: false });
      })
      .catch(() => {
        if (cancelled || reqId !== reqRef.current) return;
        setState({ path, url: null, error: true });
      });

    return () => {
      cancelled = true;
      controller.abort();
      // Revoke whatever THIS effect created — on unmount, and on every path change. The
      // consumer can no longer be rendering it: the state below is path-keyed, so the new
      // path already reads as `null`.
      if (created) URL.revokeObjectURL(created);
    };
  }, [path]);

  // A result from a previous path is not this path's answer. Reporting it would show the
  // wrong (and by now revoked) image for a frame or more.
  if (state.path !== path) return { url: null, error: false };
  return { url: state.url, error: state.error };
}
