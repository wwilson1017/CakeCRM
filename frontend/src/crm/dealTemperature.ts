/**
 * dealTemperature — the rep's own read on a deal, as pure rules (issue #125).
 *
 * `deals.deal_temperature` is a real column holding `'hot' | 'warm' | 'cold'`, or NULL when
 * nobody has triaged the deal. **NULL is not `'cold'`.** Cold is a judgment someone made, and
 * the backend's scoring factor only moves a lead score for a judgment — an untriaged deal
 * applies no factor at all. Every rule here keeps those two states apart; collapsing them is
 * the one change that would silently make the feature lie.
 *
 * Everything is pure so the cycle can be tested without a DOM, and so `DealTemperatureIcon`
 * holds only rendering.
 */

/** The stored tiers, coldest to hottest — the same order and spelling as the server's
 *  `service.DEAL_TEMPERATURES` and the migration's CHECK constraint.
 *
 *  THREE tiers, where the blueprint has four (it carries a `Cool` between warm and cold).
 *  Issue #125's title and body both specify Hot/Warm/Cold, and a shorter ladder is worth
 *  real money on a control whose whole affordance is clicking through it. */
export const DEAL_TEMPERATURES = ['cold', 'warm', 'hot'] as const;

export type DealTemperature = (typeof DEAL_TEMPERATURES)[number];

/**
 * Narrow a wire value to a tier, or null.
 *
 * Anything unrecognised reads as null — "not triaged" — rather than being passed through as
 * a fifth pseudo-tier. That matches the server's `_temperature_multiplier`, which also fails
 * an unknown value toward neutral, and it is the only safe direction here: the alternative
 * is a card rendering a state with no glyph, no label and no place in the cycle.
 */
export function normalizeTemperature(value: string | null | undefined): DealTemperature | null {
  if (typeof value !== 'string') return null;
  const cleaned = value.trim().toLowerCase();
  return (DEAL_TEMPERATURES as readonly string[]).includes(cleaned)
    ? (cleaned as DealTemperature)
    : null;
}

/**
 * The next value in the click cycle: `not set → hot → warm → cold → not set`.
 *
 * Two properties, each deliberate. **An untriaged deal goes to `hot` first**, so flagging the
 * deal you care about is one click from rest — the blueprint's call, and it is right.
 * **The cycle returns to unset**, which the blueprint's does not: there, clearing a
 * temperature needs a second surface (its custom-fields dropdown), so a mis-click is
 * permanent from the board. CakeCRM has no such fallback for a column, and a control that
 * can enter a state it cannot leave is a trap.
 *
 * Descending hot → warm → cold after the first click, rather than ascending, because the
 * first click already jumped to the top of the ladder; ascending from there would mean
 * cycling through every tier twice to undo an accidental Hot.
 */
export function nextTemperature(value: string | null | undefined): DealTemperature | null {
  switch (normalizeTemperature(value)) {
    case null: return 'hot';
    case 'hot': return 'warm';
    case 'warm': return 'cold';
    case 'cold': return null;
  }
}

/** Display word for a tier, or for the untriaged state. Used in the control's `title` and
 *  `aria-label`, which are what carry the meaning to a reader who cannot resolve the glyph. */
export function temperatureLabel(value: string | null | undefined): string {
  const tier = normalizeTemperature(value);
  return tier === null ? 'not set' : tier.charAt(0).toUpperCase() + tier.slice(1);
}
