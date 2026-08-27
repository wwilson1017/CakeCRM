import { describe, expect, it } from 'vitest';

/**
 * The literal text of `src/index.css`, substituted by `define` in `vitest.config.ts` (see the
 * comment there for why neither `?raw` nor `node:fs` works from inside a test). Declared rather
 * than imported so this file needs no Node types. If the define is ever dropped, the parse below
 * throws instead of quietly measuring nothing — see `DARK_BLOCK`.
 */
declare const __INDEX_CSS__: string;
const CSS: string = typeof __INDEX_CSS__ === 'string' ? __INDEX_CSS__ : '';

/**
 * WCAG AA guard for the neutral ink ramp (issue #68).
 *
 * `--color-ck-ink{,-mute,-soft,-dim}` are ALL body-text colours — `ink-dim` alone paints every
 * form label (`shared/styles.labelStyle`), every uppercase section heading
 * (`crm/styles.sectionHeading`) and most empty states — so each has to clear AA's 4.5:1 on
 * every surface it can land on, in both themes. Before #68 none of `ink-dim`/`ink-soft` did
 * (2.14:1 at worst), and `ink-mute` failed on the composited surfaces too.
 *
 * The test reads the SHIPPED index.css rather than a copy of the palette: a duplicated table
 * would drift silently, which is the exact failure mode this is here to stop.
 *
 * "Surface" is deliberately more than the three raw background tokens. Chips and row hovers
 * composite a translucent wash over them, and that wash is what actually binds the ramp: in
 * BOTH themes the worst surface is a 6% ink chip inside a 6%-ink-hovered row — over `raised`
 * in light (4.59:1) and over `card` in dark (4.56:1). Every composite below is a pairing that
 * exists in the app; see `surfaces()` for the call site each one comes from.
 */

const AA_NORMAL_TEXT = 4.5;

// ── WCAG 2.x relative luminance / contrast ratio, sRGB ───────────────────────

type Rgb = [number, number, number];

function hexToRgb(hex: string): Rgb {
  const h = hex.replace('#', '');
  const v = h.length === 3 ? h.split('').map(c => c + c).join('') : h;
  if (!/^[0-9a-fA-F]{6}$/.test(v)) throw new Error(`not a hex colour: ${hex}`);
  return [0, 2, 4].map(i => parseInt(v.slice(i, i + 2), 16)) as Rgb;
}

function luminance([r, g, b]: Rgb): number {
  const [lr, lg, lb] = [r, g, b].map(c => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * lr + 0.7152 * lg + 0.0722 * lb;
}

function contrast(fg: Rgb, bg: Rgb): number {
  const [hi, lo] = [luminance(fg), luminance(bg)].sort((a, b) => b - a);
  return (hi + 0.05) / (lo + 0.05);
}

/**
 * `color-mix(in srgb, C pct%, transparent)` painted over an opaque backdrop — what
 * `shared/styles.tint()` produces. Mixing with `transparent` in sRGB yields colour C at
 * alpha pct/100; source-over compositing onto an opaque backdrop is then a plain lerp.
 *
 * Channels stay FLOAT — deliberately not rounded to 8-bit per layer. A browser composites a
 * whole stack in one high-precision pass, so rounding between layers models something that
 * does not happen, and it rounds in the optimistic direction: on the stacked chip-in-hovered-row
 * surface it reported 4.601:1 where exact composition gives 4.591:1. Harmless at today's values,
 * but it is exactly the wrong error to carry in a threshold guard.
 */
function over(color: Rgb, pct: number, under: Rgb): Rgb {
  const a = pct / 100;
  return [0, 1, 2].map(i => color[i] * a + under[i] * (1 - a)) as Rgb;
}

// ── Read the palette out of the real stylesheet ──────────────────────────────

/**
 * Comments stripped BEFORE any parsing. `index.css` quotes hex values and token names freely in
 * prose, and a declaration someone comments out is dead to the browser — parsing it would report
 * a colour the app never paints, and would do so in the reassuring direction.
 */
const LIVE_CSS = CSS.replace(/\/\*[\s\S]*?\*\//g, '');

/**
 * Every `--color-ck-*` declaration in a slice, keyed by name with its RAW value text. The value
 * is captured as "everything up to the semicolon" rather than matched as a hex on purpose: a
 * token whose value stops being a plain hex (an `rgb()`, a `var()`, a `color-mix()`) must show
 * up here and be REJECTED loudly, not silently vanish from the palette and take its surfaces
 * with it.
 */
function declarations(slice: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of slice.matchAll(/--color-ck-([a-z0-9-]+):\s*([^;]+);/g)) {
    out[m[1]] = m[2].trim();
  }
  return out;
}

/** The `.dark { … }` override block — matched on its own line so `.dark .hljs {` can't win. */
const DARK_BLOCK = (() => {
  const start = LIVE_CSS.indexOf('\n.dark {');
  if (start < 0) throw new Error('index.css: no `.dark {` block found');
  const end = LIVE_CSS.indexOf('\n}', start);
  if (end < 0) throw new Error('index.css: unterminated `.dark {` block');
  return LIVE_CSS.slice(start, end);
})();

const LIGHT_TOKENS = declarations(LIVE_CSS.slice(0, LIVE_CSS.indexOf('\n.dark {')));
const DARK_TOKENS = { ...LIGHT_TOKENS, ...declarations(DARK_BLOCK) };

const INK_TOKENS = ['ink', 'ink-mute', 'ink-soft', 'ink-dim'] as const;

/**
 * The pipeline stages, DERIVED from the stylesheet rather than copied from
 * `crm/constants.ts`'s `STAGE_ORDER`. Two reasons: nothing under `core/` imports from `crm/`
 * (a layering boundary this test has no business breaking), and a hand-copied list is the same
 * silent-drift bug the palette parsing above exists to avoid — add a stage token and its 12%
 * wash would otherwise ship unmeasured with CI green.
 *
 * Derived from NAMES, never from which values happened to look like a hex: a stage rewritten as
 * `rgb(…)` stays in this list and is then rejected by the palette assertion below. Filtering on
 * the value instead would let that stage drop out of the surface list silently, and the
 * self-adjusting surface count would shrink to match it.
 */
const STAGES = Object.keys(LIGHT_TOKENS)
  .filter(k => k.startsWith('stage-'))
  .map(k => k.slice('stage-'.length));

/**
 * Every background an ink-family token is painted on, per the components that render them.
 * Keys are `<wash>/<base>` so a failure names the exact composite.
 *
 * Deliberately NOT covered, because it is a different question: a brand-hue chip that carries
 * its OWN hue as text (`tint(CORAL,15)` + CORAL in `PriorityBadge urgent`, `tint(SAGE,12)` +
 * SAGE, …). Those pairs are about the status/stage hues, which #54 already tuned per theme;
 * this guard is about the neutral ramp. The one place an ink token DOES land on a brand wash
 * (`AiTouchDetail`'s banner: `INK` on `tint(GOLD,10)`) is included below.
 */
function surfaces(t: Record<string, string>): Record<string, Rgb> {
  const ink = hexToRgb(t.ink);
  const out: Record<string, Rgb> = {};
  for (const base of ['card', 'bg', 'raised'] as const) {
    const b = hexToRgb(t[base]);
    out[base] = b;

    // A single ink wash covers two things that happen to composite identically:
    //   - the row/tab hover overlay `shared/styles.HOVER`, which is itself an ink tint —
    //     5% in light (`rgba(41,41,41,.05)`) and 6% in dark (`rgba(240,239,232,.06)`);
    //   - an ink-washed chip — `tint(INK,5)` StatusBadge inactive (ink-dim text) and
    //     `tint(INK,6)` PriorityBadge low / ScorePill cool / AiTouchDetail (ink-soft text).
    // Both percentages are live in both themes, so both are checked against both.
    for (const pct of [5, 6]) {
      const wash = over(ink, pct, b);
      out[`ink${pct}/${base}`] = wash;
      // …and a chip sitting inside a hovered row stacks the two. This is the surface that
      // actually binds the ramp in both themes.
      out[`ink${pct}/ink${pct}/${base}`] = over(ink, pct, wash);
    }

    // `AiTouchDetail`'s stale/superseded banner: `tint(GOLD,10)` carrying INK.
    out[`gold10/${base}`] = over(hexToRgb(t.amber), 10, b);
    // `shared/collection`'s `hover:bg-line/50` rows, which carry `text-muted` (= ink-mute).
    out[`line50/${base}`] = over(hexToRgb(t.line), 50, b);

    // Pipeline deal cards and deal chips: `tint(STAGE_COLORS[s].color, 12)` carrying
    // ink-dim / ink-mute text (crm/constants.ts `stage()`).
    for (const s of STAGES) out[`stage12-${s}/${base}`] = over(hexToRgb(t[`stage-${s}`]), 12, b);
  }
  return out;
}

// ── The guard ────────────────────────────────────────────────────────────────

describe.each([
  ['light', LIGHT_TOKENS],
  ['dark', DARK_TOKENS],
])('%s theme neutral ink ramp', (themeName, tokens) => {
  it('parsed a complete palette out of index.css', () => {
    // Fail closed: a rename or a regex miss must break the suite, not silently check nothing.
    // STAGES is derived, so assert it is non-empty — an empty derivation would quietly drop
    // every stage wash from the surface list while the ratio test still went green.
    expect(STAGES.length, 'no --color-ck-stage-* tokens parsed from index.css').toBeGreaterThan(0);
    for (const k of [...INK_TOKENS, 'card', 'bg', 'raised', 'line', 'amber', ...STAGES.map(s => `stage-${s}`)]) {
      // Must be a literal hex: every one of these is composited numerically below, and this
      // guard cannot resolve `var()` / `rgb()` / `color-mix()`. Rewriting one of them in another
      // syntax is legitimate — it just has to fail HERE and be handled, not slip through.
      expect(tokens[k], `${themeName}: --color-ck-${k} missing from index.css, or not a literal hex`)
        .toMatch(/^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/);
    }
  });

  it('clears WCAG AA 4.5:1 on every surface it is painted on', () => {
    const surf = surfaces(tokens);
    // 3 raw + per base (2 ink washes + 2 stacked + gold + line + one per stage) × 3 bases.
    // Derived rather than hard-coded, so it still catches a key COLLISION (two compositions
    // overwriting each other) without failing every time a stage is added.
    expect(Object.keys(surf).length).toBe(3 + 3 * (2 + 2 + 2 + STAGES.length));

    const failures: string[] = [];
    for (const token of INK_TOKENS) {
      for (const [name, bg] of Object.entries(surf)) {
        const ratio = contrast(hexToRgb(tokens[token]), bg);
        if (ratio < AA_NORMAL_TEXT) failures.push(`${token} on ${name}: ${ratio.toFixed(2)}:1`);
      }
    }
    expect(failures, `${themeName}: ${failures.length} pairing(s) under ${AA_NORMAL_TEXT}:1`).toEqual([]);
  });

  it('keeps the four ramp steps ordered and visually distinct', () => {
    // Monotone in luminance away from the page background, and no two steps closer than
    // 4 CIE L* — the ramp is compressed (#68) but it still has to READ as four steps.
    const lstar = (hex: string) => {
      const y = luminance(hexToRgb(hex));
      return y > 216 / 24389 ? 116 * Math.cbrt(y) - 16 : (y * 24389) / 27;
    };
    const steps = INK_TOKENS.map(t => lstar(tokens[t]));
    const gaps = steps.slice(1).map((v, i) => v - steps[i]);
    // Light inks darken away from ink; dark inks lighten. Either way, all gaps share a sign.
    const sign = themeName === 'light' ? 1 : -1;
    for (const [i, g] of gaps.entries()) {
      expect(g * sign, `${INK_TOKENS[i]}→${INK_TOKENS[i + 1]} runs the wrong way`).toBeGreaterThan(0);
      expect(Math.abs(g), `${INK_TOKENS[i]}→${INK_TOKENS[i + 1]} is only ${Math.abs(g).toFixed(1)} L* apart`).toBeGreaterThan(4);
    }
  });
});
