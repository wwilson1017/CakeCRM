// Copy for the AI touch-count drill-down (issue #56). Pure functions, no React, so the
// wording — the part that has to be honest rather than merely correct — is unit-testable.
//
// Two rules run through all of it: never promise a refresh that cannot happen (a closed
// deal has stopped recomputing), and name what is observably wrong rather than guessing
// why (the reader can see the list; we cannot see which drift route produced it).
import type { AiTouchEventState, AiTouchVerdictState } from '../core/types';

export function stateLabel(state: AiTouchEventState, open: boolean): string {
  switch (state) {
    case 'touch':
      return 'Touch';
    case 'not_touch':
      return 'Not a touch';
    case 'excluded_empty':
      return 'Not sent to AI — empty note';
    case 'edited_since':
      return 'Edited since it was judged';
    case 'stage_move':
      return 'Not counted';
    case 'not_evaluated':
      // "Awaiting" would promise a pass that a closed deal never gets.
      return open ? 'Awaiting next AI pass' : 'Never evaluated';
  }
}

/** The honesty banner. Null when there is genuinely nothing to warn about. */
export function bannerCopy(state: AiTouchVerdictState, open: boolean): string | null {
  if (state === 'none') {
    // Reached both by counts written before #56 and by a reply whose verdicts failed
    // validation. We cannot tell which, so state the fact and claim no cause.
    return open
      ? 'No per-event explanations are stored for this count.'
      : 'No per-event explanations are stored for this count, and this deal is closed, so none will be added.';
  }
  if (state === 'superseded') {
    return "These explanations no longer add up to the count shown, so they don't fully explain it.";
  }
  if (state === 'stale') {
    return open
      // Deliberately "will be re-evaluated with the next note", not "a refresh is queued":
      // the queue is process-local and this read cannot see it.
      ? "The deal's events have changed since this was worked out. It will be re-evaluated with the deal's next note or logged activity."
      : "The deal's events changed after this was worked out, and it is closed, so the count no longer updates.";
  }
  return open ? null : 'This deal is closed, so its touch count no longer recalculates.';
}

/** Says the list is a recent window, not the deal's whole history. */
export function coverageNote(truncated: boolean): string | null {
  return truncated
    ? "Only the deal's most recent events are shown — older history isn't part of this count."
    : null;
}

export function summaryLine(
  counted: number | null,
  evaluated: number,
  computedAt: string | null,
): string {
  if (counted == null || evaluated === 0) return 'No per-event explanations stored yet.';
  const when = formatEvaluatedAt(computedAt);
  const base = `${counted} of ${evaluated} events counted as touches`;
  return when ? `${base} · evaluated ${when}` : base;
}

function formatEvaluatedAt(value: string | null): string {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  return parsed.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}
