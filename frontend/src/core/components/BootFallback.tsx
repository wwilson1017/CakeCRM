/**
 * The loading state shown while a lazy chunk is in flight (#149).
 *
 * One component for every Suspense gap — the cold boot in `Root` (platform or public
 * todo app), the route gap inside `CrmLayout`, the assistant drawer, and the auth check
 * in `ProtectedRoute` — so a chunk load and an auth check look like the same app rather
 * than two different ones.
 *
 * Theme: token classes only (`bg-ck-bg`, `text-ck-ink-mute`, `border-ck-accent-text`).
 * `index.html` sets `.dark` on the root element BEFORE React loads, and `body` already
 * paints `var(--color-ck-bg)`, so this never flashes white in dark mode. The spinner
 * ring routes through `ck-accent-text` rather than the raw brand red: on the dark
 * surface the fixed red fails contrast as a thin stroke (#54's accent-as-icon rule).
 *
 * `role="status"` + `aria-live="polite"` + the visually-hidden text is the part that is
 * easy to leave off and costs a screen-reader user the whole signal: without it a cold
 * boot on a slow connection is silence, indistinguishable from a page that never loaded.
 *
 * `variant`: 'page' fills the viewport (a whole-tree gap); 'panel' pads inside an
 * already-rendered container (the CRM content column, the assistant drawer) so the
 * chrome around it stays put and no scrollbar flashes.
 */
export default function BootFallback({ variant = 'page' }: { variant?: 'page' | 'panel' }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={`flex items-center justify-center bg-ck-bg text-ck-ink-mute ${
        variant === 'page' ? 'min-h-svh' : 'py-16'
      }`}
    >
      <div className="w-8 h-8 border-2 border-ck-accent-text border-t-transparent rounded-full animate-spin" />
      <span className="sr-only">Loading…</span>
    </div>
  );
}
