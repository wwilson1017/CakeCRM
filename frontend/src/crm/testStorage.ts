/**
 * testStorage — a localStorage shim for the test runner. Imported ONLY by test files, so it
 * never reaches a bundle.
 *
 * It exists because the runner's `localStorage` is not the same thing on every machine, and
 * #124's tests are the first here to need one. Node's own global `localStorage` is a getter
 * that stays `undefined` without `--localstorage-file`, and it SHADOWS the one jsdom would
 * otherwise install — so on a Node 26 dev machine `window.localStorage` is that same
 * undefined, while CI's Node supplies a working store. `sessionStorage` is unaffected, which
 * is why nothing in this repo needed a shim before.
 *
 * That divergence is not a detail to work around; it produced a real defect. With no
 * localStorage, `saveShowClosedStages`' write throws into its own try/catch and nothing
 * persists — so a test that turned the preference on locally left NOTHING behind, while the
 * same test on CI seeded every later test in its file. The suite was green here and red there.
 * Installing this in `beforeEach` makes both runners measure the identical store, fresh per
 * test, rather than leaving isolation to whatever the host happens to provide.
 *
 * It is the environment, never the subject: tests drive the real `pipelineBoard` functions
 * against it. `refuseWrites` reproduces private mode / quota by throwing where a browser would.
 */

/** Installs a fresh, empty localStorage. Returns the function that puts the old one back. */
export function installLocalStorage(refuseWrites = false): () => void {
  const store = new Map<string, string>();
  const shim = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => {
      if (refuseWrites) throw new Error('QuotaExceededError');
      store.set(k, String(v));
    },
    removeItem: (k: string) => { store.delete(k); },
    clear: () => { store.clear(); },
    key: (i: number) => [...store.keys()][i] ?? null,
    get length() { return store.size; },
  };
  const prev = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', { value: shim, configurable: true, writable: true });
  return () => {
    if (prev) Object.defineProperty(globalThis, 'localStorage', prev);
    else delete (globalThis as { localStorage?: unknown }).localStorage;
  };
}
