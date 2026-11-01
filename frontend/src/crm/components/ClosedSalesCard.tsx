import { useEffect, useState, type CSSProperties } from 'react';
import { api } from '../../core/api/client';
import type { CrmDeal } from '../../core/types';
import { INK, INK_DIM, LINE, CORAL_TEXT } from '../../shared/styles';
import { cardStyle, sectionHeading } from '../styles';
import { matchesClosedOnPreset } from '../pipelineFilters';
import { OwnerName } from './OwnerName';

const LIMIT = 1000;

function money(value: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(value);
  } catch {
    return `${currency} ${value.toLocaleString()}`;
  }
}

export function ClosedSalesCard({ refreshKey = 0, wrapperStyle, onOpenDeal }: {
  refreshKey?: number;
  wrapperStyle?: CSSProperties;
  onOpenDeal?: (id: number) => void;
}) {
  // Kept across a refetch: stale rows beat a "Loading" flash after every mutation.
  const [data, setData] = useState<{ deals: CrmDeal[]; truncated: boolean; failed: boolean } | null>(null);

  useEffect(() => {
    let cancelled = false;
    api<{ deals: CrmDeal[] }>('/api/crm/deals?stage=won&sort_by=closed_on&limit=1000')
      .then(({ deals }) => {
        if (cancelled) return;
        const now = new Date();
        const inWindow = (d: CrmDeal) => matchesClosedOnPreset(d, 'last7', now);
        // simplification: 1000 wins in a week is beyond this product's scale;
        // page with a closed_on cursor if that ever holds.
        const truncated = deals.length === LIMIT && inWindow(deals[deals.length - 1]);
        setData({
          failed: false,
          deals: deals.filter(inWindow).sort((a, b) =>
            (b.closed_on ?? '').localeCompare(a.closed_on ?? '') || b.id - a.id),
          truncated,
        });
      })
      .catch(() => {
        if (!cancelled) setData({ deals: [], truncated: false, failed: true });
      });
    return () => { cancelled = true; };
  }, [refreshKey]);

  const totals = new Map<string, number>();
  for (const deal of data?.deals ?? []) {
    totals.set(deal.currency, (totals.get(deal.currency) ?? 0) + deal.value);
  }

  return (
    <div style={wrapperStyle}>
      <section style={{ ...cardStyle, padding: 20 }} aria-label="Closed sales · last 7 days">
        <h2 style={sectionHeading()}>Closed sales · last 7 days</h2>
        {data?.failed ? <p role="alert" style={{ color: CORAL_TEXT }}>Couldn't load closed sales.</p>
          : !data ? <p style={{ color: INK_DIM }}>Loading closed sales…</p>
          : <>
            <div style={{ color: INK, marginBottom: 12 }}>
              <span>{data.deals.length}{data.truncated ? '+' : ''} {data.deals.length === 1 ? 'sale' : 'sales'}</span>
              {[...totals].map(([currency, value]) => (
                <span key={currency}> · {data.truncated ? 'at least ' : ''}{money(value, currency)}</span>
              ))}
            </div>
            {data.deals.length === 0
              ? <p style={{ color: INK_DIM }}>No sales closed in the last 7 days.</p>
              : data.deals.map(deal => (
                <button key={deal.id} type="button" onClick={() => onOpenDeal?.(deal.id)}
                  disabled={!onOpenDeal}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 16, width: '100%',
                    textAlign: 'left', padding: '12px 0', background: 'none',
                    border: 0, borderTop: `1px solid ${LINE}`, color: INK,
                    font: 'inherit', cursor: onOpenDeal ? 'pointer' : undefined,
                  }}>
                  <span style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>{deal.title}</span>
                  <OwnerName ownerId={deal.owner_id} />
                  <span>{money(deal.value, deal.currency)}</span>
                </button>
              ))}
          </>}
      </section>
    </div>
  );
}
