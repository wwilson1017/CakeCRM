/**
 * Which request a deal stage change makes (issue #128) — pure, so it tests in Node.
 *
 * Two hosts move a deal between stages (`PipelinePage`'s board and `CrmDashboardPage`'s
 * deal sheet), and since #128 there are two endpoints that can land a close. One function
 * so they cannot drift about which one a Mark Lost uses.
 */

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
): StageWriteRequest {
  if (stage === 'lost' && lostReason !== undefined) {
    return {
      path: `/api/crm/deals/${dealId}/mark-lost`,
      init: { method: 'POST', body: JSON.stringify({ lost_reason: lostReason }) },
    };
  }
  return {
    path: `/api/crm/deals/${dealId}`,
    init: { method: 'PUT', body: JSON.stringify({ stage }) },
  };
}
