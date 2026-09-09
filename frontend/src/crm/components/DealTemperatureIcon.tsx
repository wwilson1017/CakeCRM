/**
 * DealTemperatureIcon — the click-to-cycle temperature control (issue #125).
 *
 * One component for both pipeline views: the board card and the List row. It renders the
 * deal's stored `deal_temperature` as a small glyph and, when given `onCycle`, advances it
 * through `not set → hot → warm → cold → not set` in place. No form, no modal.
 *
 * WHY THE FOUR STATES DO NOT DIFFER BY COLOUR ALONE. Distinguishing tiers by hue would fail
 * WCAG 1.4.1 for a reader with a colour-vision deficiency, and a `title` does not fix that —
 * it helps assistive tech, not someone looking at the screen. So each state has its own
 * SHAPE, and every pair that is adjacent in the click cycle differs structurally:
 *
 *   not set → dashed hollow ring  |  vs hot:  ring vs flame        (glyph)
 *   hot     → flame glyph         |  vs warm: flame vs dot         (glyph)
 *   warm    → filled dot          |  vs cold: filled vs hollow     (fill)
 *   cold    → solid hollow ring   |  vs not set: solid vs dashed   (border style)
 *
 * The first draft gave warm and cold the same filled dot in two different hues, which is
 * exactly the failure this block claims not to have; `dealTemperatureRenderKind` exports the
 * mapping and the test walks the cycle, so the property is checked rather than asserted in a
 * comment. Colour still carries meaning on top of shape — it is just never the only carrier.
 *
 * The dashed ring for "not set" is doing a second job: it reads as an empty slot, so the
 * control looks like something waiting to be filled in rather than a fourth tier.
 *
 * TOKENS. The dots are FILL uses and take fill tokens; the flame is an ICON, so it takes the
 * `-text` token — `shared/styles.ts` states the rule directly ("`_FILL` paints a background,
 * border, dot or bar; `_TEXT` paints a glyph — text or an icon"), and #119 exists because
 * that distinction was blurred. `CORAL_TEXT` on a stage-washed deal card is a pairing
 * `core/theme/hueContrast.test.ts` already measures, so nothing new is owed there.
 */

import type { MouseEvent as ReactMouseEvent } from 'react';
import { CORAL_TEXT, GOLD_FILL, INK_DIM, LINE_STRONG, tint } from '../../shared/styles';
import { IconFlame } from '../../shared/icons';
import {
  type DealTemperature,
  nextTemperature,
  normalizeTemperature,
  temperatureLabel,
} from '../dealTemperature';

/** How each state is drawn, independent of colour. Exported so the a11y test can walk the
 *  click cycle and assert that no two ADJACENT states share a kind — the property the doc
 *  block above claims, which is otherwise only checkable by eye and only in one theme. */
export type TemperatureRenderKind = 'flame' | 'filled-dot' | 'ring' | 'dashed-ring';

export function dealTemperatureRenderKind(tier: DealTemperature | null): TemperatureRenderKind {
  switch (tier) {
    case 'hot': return 'flame';
    case 'warm': return 'filled-dot';
    case 'cold': return 'ring';
    case null: return 'dashed-ring';
  }
}

const DOT_SIZE = 8;

function TierGlyph({ tier }: { tier: DealTemperature | null }) {
  if (tier === 'hot') return <IconFlame size={14} style={{ color: CORAL_TEXT }} aria-hidden />;
  const kind = dealTemperatureRenderKind(tier);
  return (
    <span
      aria-hidden
      data-temperature-glyph={kind}
      style={{
        width: DOT_SIZE,
        height: DOT_SIZE,
        borderRadius: '50%',
        flexShrink: 0,
        // Warm is the only filled dot. Cold is a solid ring — "gone quiet" reads better as an
        // outline than as a third saturated dot, and it is what keeps warm and cold apart
        // without a hue comparison. Not-set is the same ring, dashed and lighter.
        background: kind === 'filled-dot' ? GOLD_FILL : 'transparent',
        border: kind === 'filled-dot'
          ? undefined
          : kind === 'ring'
            ? `1.5px solid ${tint(INK_DIM, 70)}`
            : `1px dashed ${LINE_STRONG}`,
        // Sized to the flame so the four states occupy one box and a cycle does not reflow
        // the metadata row around it.
        boxSizing: 'border-box',
      }}
    />
  );
}

const boxStyle = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: 14,
  height: 14,
  flexShrink: 0,
} as const;

export default function DealTemperatureIcon({ value, onCycle, disabled = false }: {
  value: string | null | undefined;
  /** Omit for a read-only rendering (no button, no tab stop) — a surface with no writer must
   *  not advertise a control that does nothing. */
  onCycle?: (next: DealTemperature | null) => void;
  disabled?: boolean;
}) {
  const tier = normalizeTemperature(value);
  const label = `Deal temperature: ${temperatureLabel(value)}`;

  if (!onCycle) {
    // `role="img"` + a label, because the glyph is the only thing carrying the value here.
    return <span role="img" aria-label={label} title={label} style={boxStyle}><TierGlyph tier={tier} /></span>;
  }

  const upcoming = temperatureLabel(nextTemperature(value));

  // ONE handler doing both jobs, deliberately not the page's `stopCardInteraction` spread
  // plus a separate `onClick`. That spread carries its own `onClick`, so whichever of the two
  // is written second in JSX wins and the other is silently dropped — either the cycle never
  // fires, or the click bubbles and the card opens the deal sheet underneath it.
  const handleClick = (e: ReactMouseEvent) => {
    e.stopPropagation();
    if (disabled) return;
    onCycle(nextTemperature(value));
  };

  // Deliberately NO in-flight lock here. The caller patches optimistically, so the next
  // render already shows the new tier and a further click is the legitimate next step
  // through the cycle — blocking until the PUT resolved would make cold → warm → hot feel
  // broken. Repeat clicks on the SAME deal are serialized by `PipelinePage`'s per-deal write
  // chain, which is where that concern belongs and where the stage write already handles it.

  return (
    <button
      type="button"
      disabled={disabled}
      onClick={handleClick}
      // Keydown is stopped but NOT handled: a <button> already activates on Enter and Space,
      // so acting here too would fire the cycle twice. Stopping it keeps the keystroke from
      // also reaching the card's own Enter/Space "open the deal" handler.
      onKeyDown={e => e.stopPropagation()}
      onPointerDown={e => e.stopPropagation()}
      title={`${label} — click to set ${upcoming}`}
      aria-label={`${label}. Click to set ${upcoming}.`}
      style={{
        ...boxStyle,
        background: 'none',
        border: 'none',
        padding: 0,
        cursor: disabled ? 'default' : 'pointer',
        color: 'inherit',
      }}
    >
      <TierGlyph tier={tier} />
    </button>
  );
}
