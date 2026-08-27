import { readFileSync } from 'node:fs'
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
  // The literal text of the shipped stylesheet, for `core/theme/inkContrast.test.ts` — the WCAG
  // guard has to measure the palette that actually ships, and a second copy of it in the test
  // would drift silently, which is the exact bug that guard exists to prevent.
  //
  // Injected here rather than imported in the test, because neither obvious route works:
  //   • `import css from '../../index.css?raw'` resolves to an EMPTY STRING under vitest —
  //     `test.css` defaults to false, which stubs every CSS import, `?raw` included.
  //   • `node:fs` inside the test would need `"node"` in `tsconfig.app.json`'s `types`, which
  //     pins `["vite/client"]` on purpose; widening it would let browser code reference Node
  //     globals with tsc's blessing, app-wide, to serve one test.
  // This file is not in any tsconfig's `include`, so the Node import here costs nothing.
  define: {
    __INDEX_CSS__: JSON.stringify(readFileSync(new URL('./src/index.css', import.meta.url), 'utf8')),
  },
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

    // Pin the timezone so local runs and CI agree — date-derived assertions otherwise pass on one
    // machine and fail on another purely from the runner's offset.
    //
    // Deliberately NOT 'UTC', which looks like the neutral choice and is the wrong one here.
    // `crm/pipelineFilters.ts` builds its `ymd` from local-date getters precisely because
    // `toISOString()` "would drift a day in US evening time" — its own docstring says so. Under a
    // UTC runner local and UTC agree, so a test pinning that distinction would keep passing even
    // if someone regressed `ymd` back to `toISOString()`: a guard that cannot fail. Any zone
    // offset from UTC restores the distinction; this one matches the case the code documents.
    //
    // Set declaratively rather than as a `TZ=… vitest` shell prefix so it is cross-platform and
    // stays out of package.json.
    env: { TZ: 'America/Chicago' },
  },
})
