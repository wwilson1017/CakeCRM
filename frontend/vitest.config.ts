import { defineConfig } from 'vitest/config'

/**
 * Frontend unit-test runner.
 *
 * Deliberately a STANDALONE config rather than a `test` block inside `vite.config.ts`.
 * Vitest reads this file *instead of* `vite.config.ts` when it exists, which buys two things:
 *   1. `vite.config.ts` stays exactly the production build config — tests have no business
 *      inheriting its dev-server proxy.
 *   2. Tests skip the react + tailwind plugin pipeline they don't need, so the suite is fast.
 *
 * The trade-off, stated so the next person isn't surprised: this config does NOT inherit
 * `vite.config.ts`'s resolve aliases, `define` values, or custom transforms. There are none
 * today, which is why the standalone form is safe. If any are added there, bridge the two with
 * `mergeConfig(viteConfig, defineConfig({ test: { … } }))` from 'vitest/config' rather than
 * hand-copying them.
 *
 * Every option below MUST stay nested under `test` — vitest silently ignores these keys at the
 * top level, which would turn each guard into a no-op that still looks configured.
 */
export default defineConfig({
  test: {
    // `node` is the default because most targets are pure functions, and node-env tests are
    // faster and better isolated. DOM tests opt in per-file with a `// @vitest-environment jsdom`
    // docblock (see shared/listview/ViewSwitcher.test.tsx). Keep adding them that way — a
    // docblock, never a flip of this default.
    environment: 'node',

    include: ['src/**/*.{test,spec}.{ts,tsx}'],

    // ── Three fail-closed guards. A runner that reports "0 tests / exit 0" is a permanent
    // silent green, which is strictly worse than no runner because it *looks* like coverage.
    // NONE of these lines is redundant — do not delete them as "restating defaults":
    //   • `passWithNoTests: false` matches today's default; written out so the intent survives
    //     a future default change. Closes the "no test files matched" class (a moved directory).
    //   • `allowOnly: false` — vitest already fails on `.only` when CI=true, but the default
    //     leaves it silently allowed LOCALLY, which is where a stray `.only` gets committed in
    //     the first place. Fail everywhere instead.
    //   • `expect.requireAssertions` is NOT the default. It closes the companion class: a test
    //     that ran but executed zero assertions (an assertion-free body, or a conditional path
    //     that skipped every expect()).
    passWithNoTests: false,
    allowOnly: false,
    expect: { requireAssertions: true },

    // Pin the timezone so local runs and CI agree. Date-derived assertions otherwise pass on one
    // machine and fail on another purely from the runner's offset. Set declaratively rather than
    // as a `TZ=… vitest` shell prefix so it is cross-platform and stays out of package.json.
    env: { TZ: 'UTC' },
  },
})
