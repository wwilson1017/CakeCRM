import { describe, expect, it } from 'vitest';

/**
 * The literal text of `src/index.css`, substituted by `define` in `vitest.config.ts` — same
 * mechanism `inkContrast.test.ts` uses, and for the same reason (neither `?raw` nor `node:fs`
 * works from inside a test). Declared rather than imported so this file needs no Node types.
 */
declare const __INDEX_CSS__: string;
const CSS: string = typeof __INDEX_CSS__ === 'string' ? __INDEX_CSS__ : '';

/**
 * WCAG AA guard for the status, stage and brand hues used as TEXT (issue #119).
 *
 * The sibling `inkContrast.test.ts` guards the NEUTRAL ramp. This one guards the coloured half,
 * and the two are deliberately separate files rather than one: they measure different token
 * families against different surface models, and #68's file already carries a page of reasoning
 * about the ink ramp that has nothing to do with hues.
 *
 * Every hue is TWO tokens (see the FILL vs TEXT block in index.css): `--color-ck-<hue>` paints a
 * background, border, dot or bar, and `--color-ck-<hue>-text` paints a glyph. Before #119 there
 * was one token doing both jobs, and in light mode all eleven failed AA as text — worst
 * `ScorePill` warm at 2.45:1 on a stage-washed deal card. `.dark` had been tuned by #54 but only
 * against SAME-hue pairings, so it failed two cross-hue ones the app really renders.
 *
 * WHAT THIS MEASURES, and the one thing to keep straight if you touch it: the wash under a hue
 * glyph is mixed from the FILL token, which does not move when the text token is retuned. So
 * `surfaces()` composites `tokens[hue]` while the ratio is taken against `tokens[hue + '-text']`.
 * Mixing the wash from the text token instead would model a chip that darkens in step with its
 * own label — which is exactly the coupling the split removed, and it would report contrast the
 * app never achieves.
 *
 * SURFACE MODEL. Two rules, inherited from `inkContrast.test.ts`:
 *   - a hue's OWN wash is cross-producted over `card`/`bg`/`raised`, at the percentages that hue
 *     is really painted at (`OWN_WASH_PCTS`) — chips move between containers freely, and
 *     enumerating exact placements is a list to keep extending;
 *   - a STACKED wash is enumerated only where a component really produces it.
 *
 * Deliberately NOT modelled, and this is the one place this guard is LOOSER than its sibling: a
 * hue chip inside an ink-hovered row. #68 keeps that stack for the ink ramp as documented
 * headroom against a surface nothing paints. Extending the same courtesy here is not free — it
 * is the difference between `stage-lead-text` at #2d6096 and something visibly darker, to clear
 * a pairing no component renders. If a row-hover-by-ink-tint producer ever comes back (there is
 * none since #77 moved the lists onto the collection layer's opaque `hover:bg-sand`), add it
 * here and expect the values to move.
 *
 * Values are DERIVED, not chosen: each `-text` token is the smallest OKLCH lightness step from
 * its fill that clears 4.5:1 on every surface below, targeted at ~4.55 so a value is not one
 * rounding from red. Two sit at their #54-chosen edge instead and are called out in index.css.
 */

const AA_NORMAL_TEXT = 4.5;
/** WCAG 1.4.11: icons and the parts of a control that convey its state. */
const AA_NON_TEXT = 3;

// ── WCAG 2.x relative luminance / contrast ratio, sRGB ───────────────────────
// Duplicated from inkContrast.test.ts rather than extracted to a shared module: these are the
// spec's own formulas, they cannot drift, and a test that imports its own oracle from app code
// can be broken by a change to that code. Each guard measuring independently is the point.

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

/** `tint()` painted over an opaque backdrop. Channels stay FLOAT — see inkContrast.test.ts. */
function over(color: Rgb, pct: number, under: Rgb): Rgb {
  const a = pct / 100;
  return [0, 1, 2].map(i => color[i] * a + under[i] * (1 - a)) as Rgb;
}

// ── Read the palette out of the real stylesheet ──────────────────────────────

/** Comments stripped BEFORE parsing — index.css quotes hex values freely in prose. */
const LIVE_CSS = CSS.replace(/\/\*[\s\S]*?\*\//g, '');

function declarations(slice: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of slice.matchAll(/--color-ck-([a-z0-9-]+):\s*([^;]+);/g)) {
    out[m[1]] = m[2].trim();
  }
  return out;
}

const DARK_BLOCK = (() => {
  const start = LIVE_CSS.indexOf('\n.dark {');
  if (start < 0) throw new Error('index.css: no `.dark {` block found');
  const end = LIVE_CSS.indexOf('\n}', start);
  if (end < 0) throw new Error('index.css: unterminated `.dark {` block');
  return LIVE_CSS.slice(start, end);
})();

const LIGHT_TOKENS = declarations(LIVE_CSS.slice(0, LIVE_CSS.indexOf('\n.dark {')));
const DARK_TOKENS = { ...LIGHT_TOKENS, ...declarations(DARK_BLOCK) };

/**
 * Resolve a token to a literal hex, following `var(--color-ck-X)` chains.
 *
 * This is the capability `inkContrast.test.ts` does not have and does not need. It is needed
 * here because most dark `-text` tokens are declared as `var(--color-ck-<fill>)` passthroughs —
 * dark's hues were already tuned as text by #54, and a passthrough means a future retune of the
 * fill carries automatically instead of silently leaving the text token behind.
 *
 * FAILS CLOSED, in both directions a resolver can go wrong: an unknown target, a non-`var`
 * non-hex value (an `rgb()`, a `color-mix()`), and a reference cycle all throw rather than
 * returning something plausible. A guard that quietly resolved a bad chain to a passing colour
 * would be worse than no guard.
 */
function resolve(tokens: Record<string, string>, name: string, seen: string[] = []): string {
  if (seen.includes(name)) throw new Error(`index.css: --color-ck-${name} is a var() cycle (${[...seen, name].join(' → ')})`);
  const raw = tokens[name];
  if (raw === undefined) throw new Error(`index.css: --color-ck-${name} is not declared`);
  const varRef = /^var\(\s*--color-ck-([a-z0-9-]+)\s*\)$/.exec(raw);
  if (varRef) return resolve(tokens, varRef[1], [...seen, name]);
  if (!/^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(raw)) {
    throw new Error(`index.css: --color-ck-${name} is neither a hex nor a var() chain to one (got ${raw})`);
  }
  return raw;
}

/**
 * `--color-ck-accent-soft`'s mix percentage, read rather than assumed: it is 9% in light and 20%
 * in dark, and it is mixed from `accent` (the brand fill), NOT from `accent-text`. Getting that
 * source wrong is not hypothetical — it is the bug #119 found in `MemoryPage`, where a wash
 * mixed from the foreground token moved every time the foreground was retuned.
 */
function accentSoftPercent(tokens: Record<string, string>): number {
  const raw = tokens['accent-soft'] ?? '';
  const m = /^color-mix\(\s*in\s+srgb\s*,\s*var\(\s*--color-ck-accent\s*\)\s*([\d.]+)%\s*,\s*transparent\s*\)$/.exec(raw);
  if (!m) throw new Error(`index.css: could not read --color-ck-accent-soft as a mix of --color-ck-accent (got ${raw})`);
  return Number(m[1]);
}

// ── Which hues exist, and their real wash percentages ────────────────────────

const BASES = ['card', 'bg', 'raised'] as const;

/** Base stage tokens — `-text` excluded, exactly as `inkContrast.test.ts` derives them. */
const STAGES = Object.keys(LIGHT_TOKENS)
  .filter(k => k.startsWith('stage-') && !k.endsWith('-text'))
  .map(k => k.slice('stage-'.length));

const STATUS_HUES = ['green', 'amber', 'red', 'ai'] as const;
const HUES = [...STATUS_HUES, ...STAGES.map(s => `stage-${s}`)];

/**
 * The `tint(<hue>, N)` percentages each hue is painted at as a background carrying ITS OWN
 * glyph. Enumerated per hue rather than as one shared superset, because here the superset is not
 * free: holding `ai` to `red`'s 15% wash would darken it for a chip nothing renders.
 *
 * red    6  `LoadError`; 8 `StatusBadge archived`; 10 `crm/styles.btnDanger`;
 *          12 `PriorityBadge high` + `NoteComposer`; 15 `PriorityBadge urgent`
 * amber  8  `CrmLayout`'s sample-data banner; 10 `PriorityBadge medium`;
 *          12 `ScorePill warm` + `TouchCountPill low` + that banner's button
 * green 12  `StatusBadge active`, `ScorePill hot`, `TouchCountPill mid`, `AiTouchDetail`
 * ai    12  `AiBadge`, `TouchCountPill high`
 * stage 12  every stage chip (`crm/constants.stage()`)
 *
 * Green's OTHER wash — `tint(SAGE_FILL, 20)` on `listColumns`' completed-task checkbox — is
 * deliberately absent from this table and asserted separately at the NON-TEXT threshold below.
 * It is listed as an exclusion rather than simply left out, because "a percentage nobody wrote
 * down" is exactly how a real surface goes unmeasured.
 */
const OWN_WASH_PCTS: Record<string, number[]> = {
  red: [6, 8, 10, 12, 15],
  amber: [8, 10, 12],
  green: [12],
  ai: [12],
  ...Object.fromEntries(STAGES.map(s => [`stage-${s}`, [12]])),
};

/** Hues whose chip rides inside a stage-washed `DealBoardCard` — ScorePill / TouchCountPill. */
const ON_DEAL_CARD = ['amber', 'green', 'ai'];

/**
 * Hues painted directly on SOME OTHER hue's stage wash: `CrmDashboardPage`'s "Nd idle" label,
 * which is coral or amber on whatever stage the row happens to be. This is the cross-hue pairing
 * #54's same-hue sweep missed, and it is what makes dark `red-text` a real value rather than a
 * passthrough. Stage hues are NOT in this list — a stage label only ever lands on its own wash.
 */
const ON_FOREIGN_STAGE_ROW = ['red', 'amber'];

/**
 * `CrmLayout`'s sample-data banner, the one place a hue wash is nested INSIDE another wash of
 * the same hue: the banner is `tint(GOLD_FILL, 8)` over the page, and its "clear" button is
 * `tint(GOLD_FILL, 12)` inside that, carrying GOLD_TEXT. The banner also swaps its own label to
 * CORAL_TEXT on failure, which is the only place red text lands on an amber wash.
 *
 * Both pass comfortably today (worst 4.53:1), so unlike the deal-card stack this costs no design
 * change — it is modelled so a future retune of amber cannot break it silently.
 */
const BANNER_WASH_PCT = 8;

function surfaces(hue: string, tokens: Record<string, string>): Record<string, Rgb> {
  const rgb = (k: string) => hexToRgb(resolve(tokens, k));
  const out: Record<string, Rgb> = {};

  for (const base of BASES) {
    out[base] = rgb(base);
    for (const pct of OWN_WASH_PCTS[hue]) out[`own${pct}/${base}`] = over(rgb(hue), pct, rgb(base));
  }

  // A ScorePill / TouchCountPill inside a `DealBoardCard`, which only ever sits on a pipeline
  // column over the page `bg`. This is the binding surface for amber, and the 2.45:1 the issue
  // reported.
  if (ON_DEAL_CARD.includes(hue)) {
    for (const s of STAGES) {
      out[`own12/stage12-${s}/bg`] = over(rgb(hue), 12, over(rgb(`stage-${s}`), 12, rgb('bg')));
    }
  }

  // The dashboard's stale + top-deal rows: a stage-washed row over the section `card`, and the
  // same shape on `bg` for the contact/company rollups.
  if (ON_FOREIGN_STAGE_ROW.includes(hue)) {
    for (const s of STAGES) {
      for (const base of ['card', 'bg'] as const) {
        out[`stage12-${s}/${base}`] = over(rgb(`stage-${s}`), 12, rgb(base));
      }
    }
  }

  // The sample-data banner (see BANNER_WASH_PCT): amber's own 12% button nested in the 8%
  // banner, and red's error label directly on that banner.
  for (const base of BASES) {
    const banner = over(rgb('amber'), BANNER_WASH_PCT, rgb(base));
    if (hue === 'amber') out[`own12/amber${BANNER_WASH_PCT}/${base}`] = over(rgb('amber'), 12, banner);
    if (hue === 'red') out[`amber${BANNER_WASH_PCT}/${base}`] = banner;
  }

  // `LoadError`: a `tint(CORAL_FILL, 6)` panel whose Retry action is GOLD_TEXT — the mirror of
  // the banner above, and the only other place one hue's glyph sits on another hue's own wash.
  if (hue === 'amber') {
    for (const base of BASES) out[`red6/${base}`] = over(rgb('red'), 6, rgb(base));
  }

  return out;
}

/** The brand accent's own surfaces. Both washes are mixed from `accent`, never `accent-text`. */
function accentSurfaces(tokens: Record<string, string>): Record<string, Rgb> {
  const rgb = (k: string) => hexToRgb(resolve(tokens, k));
  const softPct = accentSoftPercent(tokens);
  const out: Record<string, Rgb> = {};
  for (const base of BASES) {
    out[base] = rgb(base);
    // ACCENT_SOFT chips carrying ACCENT_TEXT: `QuickActions`, `crm/styles.filterTab` active.
    out[`soft${softPct}/${base}`] = over(rgb('accent'), softPct, rgb(base));
    // `MemoryPage`'s active tab: tint(ACCENT, 12) carrying ACCENT_TEXT.
    out[`accent12/${base}`] = over(rgb('accent'), 12, rgb(base));
  }
  return out;
}

// ── The guard ────────────────────────────────────────────────────────────────

describe.each([
  ['light', LIGHT_TOKENS],
  ['dark', DARK_TOKENS],
])('%s theme hue-as-text', (themeName, tokens) => {
  it('pairs every hue with exactly one -text token, in both directions', () => {
    // Fail closed. A missing `-text` would drop that hue's surfaces from the sweep entirely, and
    // the derived count below would shrink to match — green, with the suite measuring less.
    // An ORPHAN `-text` matters just as much for a different reason: `inkContrast.test.ts`
    // derives its stage list by excluding the `-text` suffix, so a token named
    // `--color-ck-stage-foo-text` with no `stage-foo` behind it means one of the two guards is
    // reading a stage the other cannot see.
    expect(STAGES.length, 'no --color-ck-stage-* tokens parsed from index.css').toBeGreaterThan(0);
    expect(HUES.length).toBe(STATUS_HUES.length + STAGES.length);

    for (const hue of [...HUES, 'accent']) {
      expect(tokens[hue], `${themeName}: --color-ck-${hue} missing`).toBeDefined();
      expect(tokens[`${hue}-text`], `${themeName}: --color-ck-${hue}-text missing`).toBeDefined();
      // Resolves to a literal hex, through any var() chain, or throws saying why.
      expect(() => resolve(tokens, `${hue}-text`)).not.toThrow();
    }

    const textTokens = Object.keys(tokens).filter(k => k.endsWith('-text'));
    const expected = new Set([...HUES, 'accent'].map(h => `${h}-text`));
    for (const t of textTokens) {
      expect(expected.has(t), `${themeName}: --color-ck-${t} has no fill token behind it`).toBe(true);
    }
    expect(textTokens.length).toBe(expected.size);
  });

  it('clears WCAG AA 4.5:1 wherever a hue is painted as text', () => {
    const failures: string[] = [];
    let checked = 0;

    for (const hue of HUES) {
      const fg = hexToRgb(resolve(tokens, `${hue}-text`));
      for (const [name, bg] of Object.entries(surfaces(hue, tokens))) {
        checked++;
        const ratio = contrast(fg, bg);
        if (ratio < AA_NORMAL_TEXT) failures.push(`${hue}-text on ${name}: ${ratio.toFixed(2)}:1`);
      }
    }

    const accentFg = hexToRgb(resolve(tokens, 'accent-text'));
    for (const [name, bg] of Object.entries(accentSurfaces(tokens))) {
      checked++;
      const ratio = contrast(accentFg, bg);
      if (ratio < AA_NORMAL_TEXT) failures.push(`accent-text on ${name}: ${ratio.toFixed(2)}:1`);
    }

    // Derived, not hard-coded, so a stage can be added without editing a number — but a key
    // COLLISION (two compositions overwriting each other) still shrinks it and fails here.
    const ownWashes = HUES.reduce((n, h) => n + OWN_WASH_PCTS[h].length, 0);
    const expected =
      HUES.length * BASES.length                        // each hue on the three raw surfaces
      + ownWashes * BASES.length                        // its own washes, over each
      + ON_DEAL_CARD.length * STAGES.length             // pills inside a stage-washed deal card
      + ON_FOREIGN_STAGE_ROW.length * STAGES.length * 2 // idle-days text on a foreign stage row
      + BASES.length * 2                                // the banner: amber's button, red's label
      + BASES.length                                    // LoadError: amber Retry on a red 6% panel
      + BASES.length * 3;                               // accent: raw + soft + the 12% tab wash
    expect(checked, 'surface count drifted — a composition key collided, or a producer list changed').toBe(expected);

    expect(failures, `${themeName}: ${failures.length} hue-as-text pairing(s) under ${AA_NORMAL_TEXT}:1`).toEqual([]);
  });

  it('keeps every -text token on the legible side of its own fill', () => {
    // A DIRECTION check, and only that — worth stating precisely, because it is tempting to
    // read it as "the text token is still the right hue". It is not: it checks luminance moved
    // the legible way (in light a text token is no LIGHTER than its fill; in dark, no darker),
    // so it catches a swapped pair or a value edited in the wrong direction, but a same-hue-
    // family value at a plausible luminance would pass. Equality is legal — most dark tokens
    // are `var()` passthroughs. Enforcing actual hue identity would need an OKLCH hue-distance
    // rule; for a hand-written 12-token palette whose every value is also swept for contrast
    // above, that is not worth the machinery.
    const sign = themeName === 'light' ? 1 : -1;
    for (const hue of [...HUES, 'accent']) {
      const fill = luminance(hexToRgb(resolve(tokens, hue)));
      const text = luminance(hexToRgb(resolve(tokens, `${hue}-text`)));
      expect((fill - text) * sign, `${themeName}: --color-ck-${hue}-text moved the wrong way from its fill`)
        .toBeGreaterThanOrEqual(0);
    }
  });

  it('keeps the completed-task tick legible on its own 20% wash', () => {
    // `listColumns`' done checkbox: a `tint(SAGE_FILL, 20)` square with a `SAGE_TEXT`
    // IconCheck in it. Held to WCAG 1.4.11's 3:1 rather than 4.5:1 because the tick is a
    // graphic conveying a control's state, not text — and the distinction is load-bearing
    // rather than a let-off: in dark, `green-text` is a `var()` passthrough to `green`, so
    // the tick and its wash are literally the same hue and land at 4.14:1. Holding an icon
    // to the text threshold would force a dark-mode literal for every green glyph in the
    // app to fix one checkbox.
    //
    // This surface is asserted HERE rather than folded into OWN_WASH_PCTS because putting a
    // 20% entry there would sweep it at 4.5:1 against every hue-as-text assertion above.
    const tick = hexToRgb(resolve(tokens, 'green-text'));
    for (const base of BASES) {
      const wash = over(hexToRgb(resolve(tokens, 'green')), 20, hexToRgb(resolve(tokens, base)));
      const ratio = contrast(tick, wash);
      expect(ratio, `${themeName}: the done tick on own20/${base} is ${ratio.toFixed(2)}:1`)
        .toBeGreaterThanOrEqual(AA_NON_TEXT);
    }
  });

  it('keeps the launcher label legible on the launcher fill', () => {
    // The floating "Ask Baker" pill paints `--color-ck-accent-launcher` as a SOLID fill under
    // an `accent-ink` label. That token is outside the fill/text hue family (it has no
    // `-text` twin — nothing paints it as a glyph), so the pairing sweep above never sees it;
    // this is its one guard. `accent-ink` is white in both themes, so darkening the fill can
    // only help — the assertion exists so a later "brighten it back" cannot slip under 4.5:1
    // the way the brand red itself sits at 4.66:1.
    const fill = hexToRgb(resolve(tokens, 'accent-launcher'));
    const ink = hexToRgb(resolve(tokens, 'accent-ink'));
    const ratio = contrast(ink, fill);
    expect(ratio, `${themeName}: accent-ink on accent-launcher is ${ratio.toFixed(2)}:1`)
      .toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
  });

  it('keeps a solid status fill legible under ON_STATUS', () => {
    // The three buttons that paint a hue as a SOLID background and put a label on it:
    // `TasksPage` "Mark Complete", `DealDetailBody` "Mark Won" (both green) and `ConfirmHost`'s
    // danger confirm (red). They use `--color-ck-on-status`, not `accent-ink`: white is correct
    // on the brand red in both themes but was only 2.49:1 on the green `.dark` lightens for text.
    const on = hexToRgb(resolve(tokens, 'on-status'));
    for (const hue of ['green', 'red'] as const) {
      const ratio = contrast(on, hexToRgb(resolve(tokens, hue)));
      expect(ratio, `${themeName}: on-status on the ${hue} fill is ${ratio.toFixed(2)}:1`)
        .toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
    }
    // ACCENT keeps ACCENT_INK, so pin that pairing here too rather than leaving it unguarded.
    const inkOnAccent = contrast(hexToRgb(resolve(tokens, 'accent-ink')), hexToRgb(resolve(tokens, 'accent')));
    expect(inkOnAccent, `${themeName}: accent-ink on the brand accent is ${inkOnAccent.toFixed(2)}:1`)
      .toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
  });
});

describe('the guard itself', () => {
  // Per the repo's rule that a sweep must be falsifiable: pin the detector on input it MUST
  // flag and input it must NOT. Without this, a regex that quietly stopped matching would read
  // as coverage forever.
  //
  // The regression injected is the REAL one rather than a synthetic colour: before #119 every
  // hue's text WAS its fill, so setting `amber-text` back to `amber` reproduces the exact defect
  // this issue fixed, against the real palette and the real surface model. A hand-picked
  // synthetic hex would only prove the arithmetic runs.
  const worstFor = (hue: string, tokens: Record<string, string>) => {
    const fg = hexToRgb(resolve(tokens, `${hue}-text`));
    return Math.min(...Object.values(surfaces(hue, tokens)).map(bg => contrast(fg, bg)));
  };

  it('flags the pre-#119 palette, where each hue was its own text colour', () => {
    for (const hue of ['amber', 'green', 'ai', 'stage-proposal']) {
      const regressed = { ...LIGHT_TOKENS, [`${hue}-text`]: LIGHT_TOKENS[hue] };
      expect(worstFor(hue, regressed), `reverting ${hue}-text to its fill should fail AA`)
        .toBeLessThan(AA_NORMAL_TEXT);
    }
  });

  it('passes the palette that actually ships', () => {
    for (const hue of ['amber', 'green', 'ai', 'stage-proposal']) {
      expect(worstFor(hue, LIGHT_TOKENS), `${hue}-text as shipped`).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
    }
  });

  it('refuses a var() chain it cannot resolve, rather than guessing', () => {
    expect(() => resolve({ 'a-text': 'var(--color-ck-nope)' }, 'a-text')).toThrow(/not declared/);
    expect(() => resolve({ 'a-text': 'color-mix(in srgb, red 5%, transparent)' }, 'a-text')).toThrow(/neither a hex/);
    expect(() => resolve({ a: 'var(--color-ck-b)', b: 'var(--color-ck-a)' }, 'a')).toThrow(/cycle/);
  });

  it('reads the real stylesheet, not an empty string', () => {
    // If the vitest `define` is ever dropped, every sweep above would measure nothing and pass.
    expect(CSS.length, 'index.css did not reach this test — check vitest.config.ts `define`').toBeGreaterThan(1000);
  });
});
