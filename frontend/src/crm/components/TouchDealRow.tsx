import type { KeyboardEvent } from 'react';

import type { CrmWeeklyTouchDeal } from '../../core/types';
import { INK, INK_MUTE, INK_DIM, LINE, FONT_DISPLAY, mono, formatNumber } from '../../shared/styles';
import { touchCountColor } from '../constants';

/**
 * One touched-deal row, shared by the Weekly Touches card and its per-rep detail page
 * (issue #146).
 *
 * Lifted out of `WeeklyTouchesCard` unchanged rather than reimplemented on the page: the
 * two surfaces show the same deals behind the same number, so a divergence in how a count
 * or a missing contact renders would read as a data difference rather than a styling one.
 *
 * The whole row is the activator when `onOpen` is given — it has no interactive child — so
 * the `role="button"` + Enter/Space handling here is the only keyboard path, which is why
 * it lives with the markup instead of at each call site.
 */
export function TouchDealRow(
  { deal, onOpen, indent = false, trailing }: {
    deal: CrmWeeklyTouchDeal;
    onOpen?: (dealId: number) => void;
    /** Nested under a rep header on the card; flat on the page and in the single-rep case. */
    indent?: boolean;
    /** Appended to the company/contact line. The page passes the touch date; the card
     *  passes nothing, so its rows render exactly as they did before #146. */
    trailing?: string;
  },
) {
  return (
    <div
      {...(onOpen ? {
        role: 'button',
        tabIndex: 0,
        onClick: () => onOpen(deal.id),
        onKeyDown: (e: KeyboardEvent<HTMLDivElement>) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onOpen(deal.id);
          }
        },
      } : {})}
      style={{
        padding: '10px 0', paddingLeft: indent ? 18 : 0,
        borderBottom: `1px solid ${LINE}`,
        display: 'flex', alignItems: 'center', gap: 12,
        cursor: onOpen ? 'pointer' : undefined,
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 14, color: INK,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>{deal.title}</div>
        <div style={{ ...mono(10, INK_MUTE), marginTop: 3 }}>
          {deal.company_name || deal.contact_name || 'No contact'}
          {trailing ? ` · ${trailing}` : ''}
        </div>
      </div>
      <div style={{ textAlign: 'right', flexShrink: 0 }}>
        {/* The test id scopes assertions about an uncomputed count to the count itself:
            a page-level text check would pass off the explanatory copy, which contains
            an em dash of its own. */}
        <div
          data-testid={`touch-count-${deal.id}`}
          title="AI-estimated touches, from recent notes & activities. Most deals close between touch 5 and 12."
          style={{
            fontFamily: FONT_DISPLAY, fontSize: 17,
            color: touchCountColor(deal.touch_count),
          }}
        >{deal.touch_count ?? '—'}</div>
        <div style={mono(10, INK_DIM)}>${formatNumber(deal.value)}</div>
      </div>
    </div>
  );
}
