/**
 * Which request a deal stage change makes (issue #128) — pure, so it tests in Node.
 *
 * Two hosts move a deal between stages (`PipelinePage`'s board and `CrmDashboardPage`'s
 * deal sheet), and since #128 there are two endpoints that can land a close. One function
 * so they cannot drift about which one a Mark Lost uses.
 */

import type { CrmDeal } from '../core/types';
import { ymd } from './pipelineFilters';

/** The request a stage change should issue: a path plus `api()`'s options. */
export interface StageWriteRequest {
  path: string;
  init: RequestInit;
}

/**
 * Build the write for moving `dealId` to `stage`.
 *
 * The endpoint is chosen by the ACTION, not by the content: `lostReason` is a string
 * (possibly empty) when the user went through the Mark Lost dialog, and `undefined` for
 * every other move — a drag, a bulk selection, a stage picked in the edit form. So an
 * explicit close with the reason left blank still takes the lifecycle verb.
 *
 * That distinction is not cosmetic. `PUT /deals/:id` with `{stage: 'lost'}` leaves
 * `probability` untouched, while `POST /deals/:id/mark-lost` runs `service.mark_deal_lost`,
 * which zeroes it and appends the reason to the notes thread. Keying off `lostReason.trim()`
 * would mean a rep who closed a deal without typing prose got a materially different
 * write from one who typed a space.
 */
export function stageWriteRequest(
  dealId: number,
  stage: string,
  lostReason?: string,
  closedOn?: string,
): StageWriteRequest {
  if (stage === 'lost' && lostReason !== undefined) {
    return {
      path: `/api/crm/deals/${dealId}/mark-lost`,
      init: { method: 'POST', body: JSON.stringify({ lost_reason: lostReason }) },
    };
  }
  return {
    path: `/api/crm/deals/${dealId}`,
    init: {
      method: 'PUT',
      // #279: Mark Won carries the day the Closed on dialog chose; the server records it.
      body: JSON.stringify(stage === 'won' && closedOn ? { stage, closed_on: closedOn } : { stage }),
    },
  };
}

/**
 * The row a board paints before the server answers a stage move (#279) — the client mirror
 * of `service.closed_on_transition`: a move INTO won takes the chosen day (else today, the
 * server's default), a move OUT of won clears it, anything else leaves it alone.
 */
export function movedDeal(
  deal: CrmDeal, stage: string, closedOn?: string, today: string = ymd(new Date()),
): CrmDeal {
  if (stage === deal.stage) return { ...deal, stage };
  if (stage === 'won') return { ...deal, stage, closed_on: closedOn || today };
  if (deal.stage === 'won') return { ...deal, stage, closed_on: null };
  return { ...deal, stage };
}
