/**
 * What actually happened during a bulk stage move, and how to say it honestly (issue #55).
 *
 * Split in two on purpose. `classifyBulkMove` decides WHAT happened from the response alone;
 * `describeBulkMove` writes the sentence, and it needs one fact the classifier cannot know
 * yet — whether the reconcile refetch that runs afterwards succeeded. Folding them together
 * would force the copy to be chosen before the board's true state is known.
 *
 * The two failures are genuinely different and must not share wording:
 *
 * - `rejected` — the server refused: `ok:false` in a 200 body, or a thrown 4xx. Nothing was
 *   written. The caller reverts the optimistic move to the board's server-confirmed truth and
 *   keeps the ids selected so the operator can fix the cause and retry.
 * - `unconfirmed` — the request threw with a 5xx or never completed. The outcome is genuinely
 *   UNKNOWN: a connection can drop after Postgres has committed but before the ack arrives.
 *   The caller must NOT revert (that would claim knowledge it doesn't have) and the notice
 *   must not auto-dismiss.
 *
 * A thrown *4xx* is emphatically not the second case: FastAPI validates before the handler
 * runs, so the request provably never reached a write. Reporting it as "we couldn't confirm"
 * would manufacture doubt about deals that certainly did not change.
 *
 * Note what is absent: any message about deals moving while their history didn't. The stage
 * events ride the deal transaction (`service.bulk_move_deals`), so that state is unreachable.
 * If this module ever needs such a message back, the atomicity guarantee has regressed.
 */

/** The `{ok, updated, updated_ids, errors}` envelope every bulk-move response carries. */
export interface BulkMoveResponse {
  ok: boolean;
  updated: number;
  updated_ids?: number[];
  errors?: string[];
}

/**
 * A request that threw, reduced to what classification needs. `status` is the HTTP status
 * when the server answered at all, absent for a transport failure.
 */
export interface ThrownRequest {
  status?: number;
  /** A human-readable reason, only when the server sent a string one. */
  reason?: string;
}

export type BulkMoveOutcome =
  | { kind: 'clean' }
  | { kind: 'skips'; skipped: number }
  | { kind: 'rejected'; reason?: string }
  | { kind: 'unconfirmed' };

export function classifyBulkMove(
  result: BulkMoveResponse | { thrown: ThrownRequest },
): BulkMoveOutcome {
  if ('thrown' in result) {
    const { status, reason } = result.thrown;
    if (status !== undefined && status >= 400 && status < 500) {
      return { kind: 'rejected', reason };
    }
    return { kind: 'unconfirmed' };
  }
  // The server answered and refused: nothing was written, and `errors` says why.
  if (!result.ok) return { kind: 'rejected', reason: result.errors?.[0] };
  // Committed. Per-deal errors alongside ok:true are benign skips — a deal archived or
  // deleted between page load and the click. The rest moved.
  const skipped = result.errors?.length ?? 0;
  return skipped > 0 ? { kind: 'skips', skipped } : { kind: 'clean' };
}

export interface BulkNotice {
  text: string;
  /**
   * True when the message must outlive a toast's auto-dismiss. Reserved for a message the
   * operator has to act on — one saying the outcome is unknown, or that the board on screen
   * may be wrong.
   */
  persistent: boolean;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

/**
 * The one place bulk-move copy is written. Returns `null` when there is nothing worth
 * telling the operator — a clean run stays silent, matching how a successful single-deal
 * drag says nothing.
 *
 * `boardReconciled` is whether the post-move refetch actually applied fresh server data.
 * When it didn't, the board may still be showing the optimistic result, and any message
 * about an imperfect run has to say so and stop auto-dismissing.
 */
export function describeBulkMove(
  outcome: BulkMoveOutcome,
  submitted: number,
  boardReconciled = true,
): BulkNotice | null {
  const stale = !boardReconciled;
  const staleWarning = ' The board could not be refreshed, so it may still show changes that were not saved.';

  if (outcome.kind === 'unconfirmed') {
    // Never "check the board": the optimistic move is still on screen and the reconcile
    // fetch can itself fail. Reloading is the one instruction that is true either way.
    return {
      text: boardReconciled
        ? `Couldn't confirm whether ${plural(submitted, 'deal')} moved. The board has been refreshed to what actually saved — the deals are still selected if you want to retry.`
        : `Couldn't confirm whether ${plural(submitted, 'deal')} moved, and the board couldn't be refreshed. Reload the page to see what saved before retrying.`,
      persistent: true,
    };
  }

  if (outcome.kind === 'rejected') {
    // Trim trailing punctuation: this sentence supplies its own full stop, and a server
    // reason ending in one would render "… per bulk move..". The in-repo reasons don't,
    // but a 4xx detail is arbitrary text.
    const cleaned = outcome.reason?.trim().replace(/[.\s]+$/, '');
    const because = cleaned ? ` — ${cleaned}` : '';
    // No stale warning here even when the refetch failed: the caller reverts the optimistic
    // move to each deal's server-confirmed stage on this branch, so the board is already
    // right without the refetch.
    return { text: `${plural(submitted, 'deal')} not moved${because}.`, persistent: false };
  }

  if (outcome.kind === 'skips') {
    return {
      text: `${plural(outcome.skipped, 'deal')} skipped (no longer found)${stale ? staleWarning : ''}`,
      // A skip tally on a reconciled board is an FYI that can auto-dismiss. Once the
      // refetch has failed, the board is still showing those deals as moved and nothing
      // else on screen says otherwise, so the disclosure has to outlive the timer.
      persistent: stale,
    };
  }

  return null;
}
