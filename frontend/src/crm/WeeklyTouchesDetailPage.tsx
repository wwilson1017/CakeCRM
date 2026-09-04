/**
 * Weekly Touches, one rep (issue #146) — the uncapped list behind a number on the card.
 *
 * The card is a KPI and caps each rep's rows; this page is the whole list, so a rep who
 * touched thirty deals can be checked rather than sampled. Each row opens the same
 * `DealDetailSheet` the dashboard opens, whose #56 evidence section explains that deal's
 * count event by event — so the chain from "3 touched" to "why 3" stays unbroken.
 *
 * Every window decision belongs to the server. The card forwards the exact instants it
 * displayed as `ws`/`we`, this page forwards them on verbatim, and the label comes back in
 * the payload — so the page can never re-resolve "last 7 days" against a later `now` and
 * list a different week than the number that was clicked.
 *
 * NO zero-keys gate here, deliberately. The card hides itself when no provider has ever
 * run, because a KPI of blank estimates is worse than no KPI; this page is reached from
 * that card or by a typed URL, its membership is keyless truth, and an uncomputed count
 * renders "—" exactly as the card's rows already do.
 */

import { useEffect, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';

import { ApiError, api } from '../core/api/client';
import type { CrmDeal, CrmWeeklyTouchDetail } from '../core/types';
import { LoadError } from '../shared/LoadError';
import { INK, INK_DIM, INK_MUTE, LINE, mono } from '../shared/styles';
import { toast } from '../shared/toast';
import { useIsMobile } from '../shared/useIsMobile';
import { DealDetailSheet } from './components/DealDetailSheet';
import { DealForm } from './components/DealForm';
import { TouchDealRow } from './components/TouchDealRow';
import { parseOwnerParam, touchDetailApiPath } from './weeklyTouches';
import { stageWriteRequest } from './dealStageWrite';
import { parseUTC } from './gtd/util';
import { cardStyle, pageHeading, pagePadding } from './styles';

/** A permanent failure (a malformed link) is not the same as a transient one, so Retry is
 *  offered for exactly one of them. `notfound` also covers an owner this page rejects
 *  before it ever fetches. */
type PageError = 'notfound' | 'invalid' | 'failed';

/** The loaded payload TOGETHER with the path it describes.
 *
 *  React Router reuses this component instance across `/crm/touches/3 → /crm/touches/4`,
 *  so state keyed only by "is there data" would render one rep's rows, or a stale 404,
 *  under the other's URL for a frame. Deriving everything from `state.path === apiPath`
 *  makes that unrepresentable — the same discipline `useAuthedBlobUrl` uses. */
interface PageState {
  path: string;
  data: CrmWeeklyTouchDetail | null;
  error: PageError | null;
}

/** Touch timestamps are UTC instants, and the card labels its filter "From (UTC)" — so
 *  formatting in the viewer's zone would show a touch made on the 16th UTC as the 15th,
 *  outside the very window it was selected by. */
function touchDate(iso: string | null): string {
  if (!iso) return '';
  const d = parseUTC(iso);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
}

export function WeeklyTouchesDetailPage() {
  const { owner } = useParams<{ owner: string }>();
  const [search] = useSearchParams();
  const isMobile = useIsMobile();

  const parsed = parseOwnerParam(owner);
  const apiPath = parsed.ok ? touchDetailApiPath(parsed.owner, search) : null;

  const [state, setState] = useState<PageState | null>(null);
  const [reloadTick, setReloadTick] = useState(0);
  const [selectedDeal, setSelectedDeal] = useState<CrmDeal | null>(null);
  const [editDeal, setEditDeal] = useState<CrmDeal | null>(null);
  const reqId = useRef(0);

  useEffect(() => {
    // Bumped BEFORE the early return: navigating from a valid owner to a malformed one
    // must invalidate the request already in flight, or its response would land and
    // render under a URL this page has already rejected.
    const id = ++reqId.current;
    if (apiPath === null) return;
    api<CrmWeeklyTouchDetail>(apiPath)
      .then(data => {
        if (id === reqId.current) setState({ path: apiPath, data, error: null });
      })
      .catch((err: unknown) => {
        if (id !== reqId.current) return;
        // 404 is "no such rep" and 4xx is a link whose window the server refused — both
        // permanent, so neither offers a Retry that could only fail again. Anything else
        // (5xx, offline) is worth retrying.
        const status = err instanceof ApiError ? err.status : 0;
        const error: PageError =
          status === 404 ? 'notfound' : status >= 400 && status < 500 ? 'invalid' : 'failed';
        setState({ path: apiPath, data: null, error });
      });
  }, [apiPath, reloadTick]);

  // Only state that describes the CURRENT url counts; anything else is the previous
  // route's answer and reads as loading.
  const current = state && state.path === apiPath ? state : null;
  const data = current?.data ?? null;
  const error: PageError | null = parsed.ok ? current?.error ?? null : 'notfound';
  const loading = parsed.ok && current === null;

  // The dashboard's own wiring, copied rather than hoisted into a hook: PipelinePage and
  // CrmDashboardPage already each carry their own copy, and a shared hook for a third
  // consumer that differs in its reload function is not yet worth the indirection.
  function openDeal(id: number) {
    api<CrmDeal>(`/api/crm/deals/${id}`)
      .then(setSelectedDeal)
      .catch(() => toast.error('Could not open that deal — it may have been deleted.'));
  }

  function reload() {
    setReloadTick(t => t + 1);
  }

  async function updateDealStage(deal: CrmDeal, stage: string, lostReason?: string) {
    try {
      const { path, init } = stageWriteRequest(deal.id, stage, lostReason);
      await api(path, init);
      setSelectedDeal(null);
      reload();
    } catch {
      toast.error('Failed to move deal.');
    }
  }

  const unassigned = data?.rep.user_id === null;

  return (
    <div style={pagePadding(isMobile)}>
      <Link
        to="/crm"
        style={{ ...mono(10, INK_MUTE), textDecoration: 'none', display: 'inline-block' }}
      >← Dashboard</Link>
      <h1 style={{ ...pageHeading(isMobile), marginTop: 10 }}>Weekly touches</h1>

      {data && (
        <div style={{ fontSize: 13, color: INK_MUTE, margin: '10px 0 4px' }}>
          {/* An unassigned bucket RENDERS, muted and italic, exactly as OwnerName does
              (#128): blank would read as a rendering fault rather than a real state. */}
          <span style={{
            color: unassigned ? INK_DIM : INK,
            fontStyle: unassigned ? 'italic' : undefined,
          }}>{data.rep.name}</span>
          {' · '}{data.window.label}
          {' · '}<span style={{ color: INK }}>{data.rep.touches}</span>
          {' of '}{data.rep.open_deals} open deals touched
        </div>
      )}

      {loading && (
        <p style={{ fontSize: 13, color: INK_MUTE, marginTop: 16 }}>Loading…</p>
      )}

      {error === 'notfound' && (
        <div style={{ ...cardStyle, padding: 24, marginTop: 16 }}>
          <p style={{ fontSize: 14, color: INK, margin: 0 }}>No such rep.</p>
          <p style={{ fontSize: 13, color: INK_MUTE, margin: '8px 0 0' }}>
            That link points at a person this install doesn't have. Open Weekly touches from
            the dashboard to see who does.
          </p>
        </div>
      )}

      {error === 'invalid' && (
        <div style={{ ...cardStyle, padding: 24, marginTop: 16 }}>
          <p style={{ fontSize: 14, color: INK, margin: 0 }}>That link's date range isn't valid.</p>
          <p style={{ fontSize: 13, color: INK_MUTE, margin: '8px 0 0' }}>
            Open Weekly touches from the dashboard and pick the range there.
          </p>
        </div>
      )}

      {error === 'failed' && (
        <LoadError label="Couldn't load weekly touches" onRetry={reload} />
      )}

      {data && (
        data.deals.length === 0 ? (
          <p style={{ fontSize: 13, color: INK_DIM, marginTop: 16 }}>
            No open deals touched in this window.
          </p>
        ) : (
          <>
            <p style={{ fontSize: 12, color: INK_MUTE, margin: '14px 0', maxWidth: 560 }}>
              Open deals edited, noted, or logged against in this window — however many times,
              each deal counts once. The number beside each deal is its AI-estimated
              <em> lifetime</em> touch count, not this window's, and is blank until one has
              been computed.
            </p>
            <div style={{ ...cardStyle, padding: '2px 20px 6px' }}>
              <div style={{ borderTop: `1px solid ${LINE}` }}>
                {data.deals.map(deal => (
                  <TouchDealRow
                    key={deal.id}
                    deal={deal}
                    onOpen={openDeal}
                    trailing={touchDate(deal.touched_at)}
                  />
                ))}
              </div>
            </div>
          </>
        )
      )}

      {selectedDeal && (
        <DealDetailSheet
          key={selectedDeal.id}
          deal={selectedDeal}
          isMobile={isMobile}
          onClose={() => { setSelectedDeal(null); reload(); }}
          onEdit={(d) => { setSelectedDeal(null); setEditDeal(d); }}
          onStageChange={updateDealStage}
          onRestored={() => { setSelectedDeal(null); reload(); }}
        />
      )}

      {editDeal && (
        <DealForm
          deal={editDeal}
          onClose={() => setEditDeal(null)}
          onSaved={() => { setEditDeal(null); reload(); }}
        />
      )}
    </div>
  );
}
