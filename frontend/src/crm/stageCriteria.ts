/**
 * stageCriteria — what it takes for a deal to belong in each pipeline stage (issue #74).
 *
 * Rendered as a popover on each board column header, so a rep can answer "should this actually
 * be in Proposal yet?" without leaving the board. Deliberately static content keyed by the
 * `STAGE_ORDER` constants rather than anything configurable: these are working definitions, and
 * a per-install editor for them is a different feature than a parity port.
 *
 * The copy is vertical-neutral on purpose. The blueprint's checklists name a specific industry's
 * artefacts; this repo goes public, so the text has to read the same for a bakery, an agency or
 * a hardware vendor. `stageCriteria.test.ts` pins that mechanically.
 */

export interface StageCriteria {
  summary: string;
  checklist: string[];
}

export const STAGE_CRITERIA: Record<string, StageCriteria> = {
  lead: {
    summary: 'A new inquiry or first contact. Not yet vetted as a real opportunity.',
    checklist: [
      'Contact name and details captured',
      'Lead source identified (referral, website, event, outreach)',
      'Rough sense of what they do and how big they are',
      'Assigned to an owner',
    ],
  },
  qualified: {
    summary:
      'Confirmed as a real opportunity worth pursuing: a genuine need, and the authority to buy.',
    checklist: [
      'Decision-maker identified',
      'Company linked, with contact details',
      'Need and timeline understood',
      'Current solution or supplier known',
      'Budget range or expected volume discussed',
      'Interest confirmed, not assumed',
    ],
  },
  proposal: {
    summary: 'An active proposal is in play: they have seen the offer and the pricing.',
    checklist: [
      'Demo, sample or trial delivered',
      'Positive feedback on fit',
      'Pricing or proposal document sent',
      'Scope and quantities agreed in outline',
      'Delivery or rollout logistics discussed',
    ],
  },
  negotiation: {
    summary:
      'Terms are being finalized. Both sides are committed; what remains is detail, not whether to proceed.',
    checklist: [
      'Pricing agreed in principle',
      'Volume and order frequency discussed',
      'Contract or purchase terms in review',
      'Start date or first order date set',
      'Any customization agreed',
      'Buyer-side approvals in progress',
    ],
  },
  won: {
    summary: 'Closed. The contract is signed or the first order is in.',
    checklist: [
      'Order received or contract signed',
      'First delivery scheduled or completed',
      'Account set up for ongoing orders',
      'Handed over to whoever owns the relationship from here',
    ],
  },
  lost: {
    summary: 'Closed without a sale. Record why, and set a follow-up if there is future potential.',
    checklist: [
      'Lost reason documented (budget, competitor, timing, no need)',
      'Follow-up date set if it is worth revisiting',
      'Anything learned noted for similar prospects',
    ],
  },
};
