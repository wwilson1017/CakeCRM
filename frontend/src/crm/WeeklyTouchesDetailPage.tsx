/**
 * Weekly Touches, one rep (issue #146) — the whole list behind a number on the card.
 *
 * The card is a KPI and caps each rep's rows; this page is the full list, so a rep who
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

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';

import { ApiError, api } from '../core/api/client';
import type { CrmDeal, CrmWeeklyTouchDetail } from '../core/types';
import { LoadError } from '../shared/LoadError';
import { INK, INK_DIM, INK_MUTE, LINE, mono } from '../shared/styles';
import { toast } from '../shared/toast';
import { useIsMobile } from '../shared/useIsMobile';
import { DealDetailSheet } from './components/DealDetailSheet';
import { DealForm } from './components/DealForm';
import { RepLabel } from './components/RepLabel';
import { TouchDealRow } from './components/TouchDealRow';
import { parseOwnerParam, touchDetailApiPath } from './weeklyTouches';
import { stageWriteRequest } from './dealStageWrite';
import { parseUTC } from './gtd/util';
import { cardStyle, pageHeading, pagePadding } from './styles';

/** A permanent failure is not the same as a transient one, so Retry is offered for
 *  exactly one of them. */
type PageError = 'notfound' | 'invalid' | 'failed';

interface PageState {
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

  // A malformed owner never reaches the view, so it can never issue a request.
  if (!parsed.ok) {
    return (
      <Frame isMobile={isMobile}>
        <NoSuchRep />
      </Frame>
    );
  }

  // KEYED ON THE REQUEST, so a different rep — or a different window on the same rep —
  // remounts the view outright and every piece of state goes with it: the list, the open
  // sheet, the edit form, the request-id refs.
  //
  // Deriving "does this state belong to the current path?" instead was not enough: it
  // HID the old sheet while the path differed but kept it in state, so /3 → /4 → /3
  // matched again and brought it back. A key is React's own answer to "reset on route
  // change", and it also keeps the reset out of an effect, which this repo's
  // react-hooks config rejects (a cascading render for something a key does for free).
  const apiPath = touchDetailApiPath(parsed.owner, search);
  return <DetailView key={apiPath} apiPath={apiPath} isMobile={isMobile} />;
}

/** The page chrome both states share, so the back link and heading render even when there
 *  is nothing to show under them. */
function Frame({ isMobile, children }: { isMobile: boolean; children: React.ReactNode }) {
  return (
    <div style={pagePadding(isMobile)}>
      <Link
        to="/crm"
        style={{ ...mono(10, INK_MUTE), textDecoration: 'none', display: 'inline-block' }}
      >← Dashboard</Link>
      <h1 style={{ ...pageHeading(isMobile), marginTop: 10 }}>Weekly touches</h1>
      {children}
    </div>
  );
}

function NoSuchRep() {
  return (
    <div style={{ ...cardStyle, padding: 24, marginTop: 16 }}>
      <p style={{ fontSize: 14, color: INK, margin: 0 }}>No such rep.</p>
      <p style={{ fontSize: 13, color: INK_MUTE, margin: '8px 0 0' }}>
        That link points at a person this install doesn't have. Open Weekly touches from
        the dashboard to see who does.
      </p>
    </div>
  );
}

function DetailView({ apiPath, isMobile }: { apiPath: string; isMobile: boolean }) {
  const [state, setState] = useState<PageState | null>(null);
  const [reloadTick, setReloadTick] = useState(0);
  const [selectedDeal, setSelectedDeal] = useState<CrmDeal | null>(null);
  const [editDeal, setEditDeal] = useState<CrmDeal | null>(null);
  const reqId = useRef(0);
  // A SECOND monotonic id, for the per-deal fetches. The list's guard cannot serve here:
  // two deal requests race each other within ONE mount, so clicking A then B has to be
  // decided by request order.
  const dealReqId = useRef(0);
  // Navigating to another rep UNMOUNTS this view (the parent re-keys), and a deal fetch
  // left in flight still settles afterwards. Its `setState` is a harmless no-op, but its
  // rejection would raise a toast about a deal on a page the user has already left.
  //
  // The flag is re-ARMED in setup, not merely cleared in cleanup: `main.tsx` renders under
  // <StrictMode>, whose development cycle is setup → cleanup → setup, so a cleanup-only
  // version leaves it false for the life of the mount and silently swallows every deal the
  // user then clicks — in development only, which is precisely where it would be met and
  // mistaken for a broken endpoint. `useLayoutEffect` so the re-arm lands before a click
  // can be handled.
  const alive = useRef(true);
  useLayoutEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  useEffect(() => {
    // Still needed within a mount: `reloadTick` refetches the same path, so two responses
    // can be in flight at once even though the URL never changed.
    const id = ++reqId.current;
    api<CrmWeeklyTouchDetail>(apiPath)
      .then(data => {
        if (id === reqId.current) setState({ data, error: null });
      })
      .catch((err: unknown) => {
        if (id !== reqId.current) return;
        // 404 is "no such rep" and any other 4xx is a link whose window the server
        // refused — both permanent, so neither offers a Retry that could only fail again.
        // Anything else (5xx, offline) is worth retrying.
        const status = err instanceof ApiError ? err.status : 0;
        const error: PageError =
          status === 404 ? 'notfound' : status >= 400 && status < 500 ? 'invalid' : 'failed';
        setState({ data: null, error });
      });
  }, [apiPath, reloadTick]);

  const data = state?.data ?? null;
  const error = state?.error ?? null;
  const loading = state === null;

  // The dashboard's own wiring, copied rather than hoisted into a hook: PipelinePage and
  // CrmDashboardPage already each carry their own copy, and a shared hook for a third
  // consumer that differs in its reload function is not yet worth the indirection.
  function openDeal(id: number) {
    const req = ++dealReqId.current;
    api<CrmDeal>(`/api/crm/deals/${id}`)
      .then(deal => {
        if (alive.current && req === dealReqId.current) setSelectedDeal(deal);
      })
      // The guard covers the error path too: a failed fetch for a deal the user has
      // already dismissed — or navigated away from — must not raise a toast about it.
      .catch(() => {
        if (alive.current && req === dealReqId.current) {
          toast.error('Could not open that deal — it may have been deleted.');
        }
      });
  }

  function reload() {
    setReloadTick(t => t + 1);
  }

  /** Also invalidates any deal fetch still in flight, so a slow one cannot reopen the
   *  sheet the user just dismissed. */
  function closeModals() {
    dealReqId.current += 1;
    setSelectedDeal(null);
    setEditDeal(null);
  }

  async function updateDealStage(deal: CrmDeal, stage: string, lostReason?: string) {
    try {
      const { path, init } = stageWriteRequest(deal.id, stage, lostReason);
      await api(path, init);
      closeModals();
      reload();
    } catch {
      toast.error('Failed to move deal.');
    }
  }

  return (
    <Frame isMobile={isMobile}>
      {data && (
        <div style={{ fontSize: 13, color: INK_MUTE, margin: '10px 0 4px' }}>
          {/* Shared with the card's rep rows, so a bucket cannot be spelled or styled
              two ways depending on which surface you reached it from. */}
          <RepLabel name={data.rep.name} unassigned={data.rep.user_id === null} />
          {' · '}{data.window.label}
          {' · '}<span style={{ color: INK }}>{data.rep.touches}</span>
          {' of '}{data.rep.open_deals} open deals touched
        </div>
      )}

      {loading && (
        <p style={{ fontSize: 13, color: INK_MUTE, marginTop: 16 }}>Loading…</p>
      )}

      {error === 'notfound' && <NoSuchRep />}

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
                {/* This page's contract is the full list, so a prefix has to announce
                    itself — silently showing the first N would be the dishonest version. */}
                {data.truncated && (
                  <div style={{ ...mono(10, INK_DIM), padding: '10px 0' }}>
                    Showing the first {data.deals.length} touched deals. Narrow the range on
                    the dashboard to see the rest.
                  </div>
                )}
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
          onClose={() => { closeModals(); reload(); }}
          onEdit={(d) => {
            dealReqId.current += 1;
            setSelectedDeal(null);
            setEditDeal(d);
          }}
          onStageChange={updateDealStage}
          onRestored={() => { closeModals(); reload(); }}
        />
      )}

      {editDeal && (
        <DealForm
          deal={editDeal}
          onClose={() => setEditDeal(null)}
          onSaved={() => { closeModals(); reload(); }}
        />
      )}
    </Frame>
  );
}
