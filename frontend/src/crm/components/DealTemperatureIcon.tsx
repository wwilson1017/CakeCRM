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
 * IT OWNS ITS OWN OPTIMISM, and that is what makes the cycle usable rather than a nicety.
 * Without it a click renders nothing until the PUT lands, so three quick clicks all compute
 * `nextTemperature(<the same unchanged prop>)` and the user gets ONE step instead of three —
 * the control would simply look broken. Painting here rather than in each host also means the
 * board, the List and all three deal-sheet hosts behave identically, without every host
 * learning to patch this column.
 *
 * The override rule is #150's, arrived at there the hard way: an override is released when
 * ITS OWN write settles, success or failure — never because the incoming prop disagrees with
 * it. A prop is not evidence about a write still in flight, and an EARLIER write of this
 * card's own answering first carries exactly that disagreement, so releasing on disagreement
 * flashes the glyph back to the value the override exists to hide. The `op` token is the
 * other half: a superseded write must not clear the newer override when it settles.
 *
 * TOKENS. The dots are FILL uses and take fill tokens; the flame is an ICON, so it takes the
 * `-text` token — `shared/styles.ts` states the rule directly ("`_FILL` paints a background,
 * border, dot or bar; `_TEXT` paints a glyph — text or an icon"), and #119 exists because
 * that distinction was blurred. `CORAL_TEXT` on a stage-washed deal card is a pairing
 * `core/theme/hueContrast.test.ts` already measures, so nothing new is owed there.
 */

import { useRef, useState } from 'react';
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
   *  not advertise a control that does nothing.
   *
   *  May return a promise; when it does, the optimistic glyph is held until that promise
   *  settles. A caller whose write can FAIL should say so to the user itself (the board
   *  toasts) — this component only puts the glyph back, which on its own is a silent revert. */
  onCycle?: (next: DealTemperature | null) => void | Promise<unknown>;
  disabled?: boolean;
}) {
  // What this control last asked for, held until that request settles. See the override rule
  // in the header: released on ITS OWN settle, never on a disagreeing prop.
  const [pending, setPending] = useState<{ tier: DealTemperature | null } | null>(null);
  const opRef = useRef(0);

  const tier = pending ? pending.tier : normalizeTemperature(value);
  const shown = pending ? pending.tier : value;
  const label = `Deal temperature: ${temperatureLabel(shown)}`;

  if (!onCycle) {
    // `role="img"` + a label, because the glyph is the only thing carrying the value here.
    return <span role="img" aria-label={label} title={label} style={boxStyle}><TierGlyph tier={tier} /></span>;
  }

  const upcoming = temperatureLabel(nextTemperature(shown));

  // ONE handler doing both jobs, deliberately not the page's `stopCardInteraction` spread
  // plus a separate `onClick`. That spread carries its own `onClick`, so whichever of the two
  // is written second in JSX wins and the other is silently dropped — either the cycle never
  // fires, or the click bubbles and the card opens the deal sheet underneath it.
  const handleClick = (e: ReactMouseEvent) => {
    e.stopPropagation();
    if (disabled) return;
    // Stepping from what is SHOWN, not from the prop: mid-cycle the prop still holds the
    // pre-click value, so stepping from it would make every click after the first a no-op.
    const next = nextTemperature(shown);
    const op = opRef.current + 1;
    opRef.current = op;
    setPending({ tier: next });
    void Promise.resolve(onCycle(next))
      .catch(() => {})   // the caller owns reporting; this only decides when to stop painting
      .finally(() => {
        // Only if this write is still the latest. A superseded one clearing the override
        // would flash the glyph back to a value the user has already clicked past.
        if (opRef.current === op) setPending(null);
      });
  };

  // Deliberately NO in-flight lock. Blocking until the PUT resolved would make cold → warm →
  // hot feel broken, and there is nothing to protect: repeat clicks on the SAME deal are
  // serialized by the host's per-deal write chain, which is where that concern belongs and
  // where the stage write already handles it.

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
      // A disabled control names the tier and stops there. Keeping "click to set …" on an
      // archived deal describes an action that cannot happen — AT does announce the button as
      // unavailable alongside it, so nobody is actively misled, but the sentence is simply
      // untrue and a hovering pointer gets the same wrong promise from `title`.
      title={disabled ? label : `${label} — click to set ${upcoming}`}
      aria-label={disabled ? label : `${label}. Click to set ${upcoming}.`}
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
