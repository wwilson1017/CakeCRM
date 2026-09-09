/**
 * Weekly Touches, one rep (issue #146) — the whole list behind a number on the card.
 *
 * The card is a KPI and caps each rep's rows; this page is the full list, so a rep who
 * touched thirty deals can be checked rather than sampled. Each row opens the same
 * `DealDetailBody` the dashboard opens, inside the shared `CollectionDetail` shell — its #56
 * evidence section explains that deal's
 * count event by event — so the chain from "3 touched" to "why 3" stays unbroken.
 *
 * Every window decision belongs to the server. This page forwards the card's `start`/`end`
 * calendar days when a custom range is applied and nothing at all on the rolling default,
 * and the label comes back in the payload — so it asks the card's own question, resolved at
 * the moment the page opens.
 *
 * It deliberately does NOT inherit the card's exact instants, which an earlier revision did.
 * Freezing the bounds looks like it guarantees the page lists what the clicked number
 * counted; it cannot, because membership is "this deal's CURRENT most recent touch falls in
 * the window". A touch made after the card rendered moves the deal past a frozen upper bound
 * and deletes it from this page — including a touch made FROM this page, so logging a call
 * made the deal you had just worked disappear from the list of deals you touched.
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
import { CollectionDetail, denyEscapeBackdrop } from '../shared/collection';
import { DealDetailBody, type DealPatch } from './components/DealDetailBody';
import { DEAL_DETAIL_CONFIG } from './dealDetailConfig';
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

// Module scope so their identity is stable: the layer memoizes off these props.
const EMPTY_DEALS: CrmDeal[] = [];
const EMPTY_NAV: number[] = [];

function DetailView({ apiPath, isMobile }: { apiPath: string; isMobile: boolean }) {
  const [state, setState] = useState<PageState | null>(null);
  const [reloadTick, setReloadTick] = useState(0);
  // The open deal is an ID: `CollectionDetail` resolves it through `DEAL_DETAIL_CONFIG.loadById`
  // and owns the request, its race guard and its failure UI. That replaced this page's own
  // fetch-then-store pair — with it went a second monotonic request id, an alive ref re-armed
  // under StrictMode, and a "could not open that deal" toast; a deleted deal now gets the layer's
  // "Record unavailable · Retry" panel instead, which says the same thing where the user is
  // looking. NONE of these rows is ever in `items`: this page lists per-rep summaries, so every
  // open goes through that fetch.
  const [selectedDealId, setSelectedDealId] = useState<number | null>(null);
  const reqId = useRef(0);

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

  function reload() {
    setReloadTick(t => t + 1);
  }

  async function updateDealStage(deal: CrmDeal, stage: string, lostReason?: string) {
    try {
      const { path, init } = stageWriteRequest(deal.id, stage, lostReason);
      await api(path, init);
      // Dismiss ONLY if this deal is still the one on screen — the ‹ › arrows stay live while
      // the write is in flight, and a slow one must not close a deal the user walked to.
      setSelectedDealId(prev => (prev === deal.id ? null : prev));
      reload();
    } catch {
      toast.error('Failed to move deal.');
    }
  }

  // Rejects rather than reporting, so the inline form keeps the draft on screen and says why —
  // it is the only copy of what the user typed. Nothing is patched in place here: no row on this
  // page is a deal record, so the reload is the whole reconciliation.
  async function saveDeal(deal: CrmDeal, patch: DealPatch): Promise<CrmDeal> {
    const updated = await api<CrmDeal>(`/api/crm/deals/${deal.id}`, {
      method: 'PUT', body: JSON.stringify(patch),
    });
    reload();
    // Handed back so the panel folds the SERVER's row rather than the patch it sent — the route
    // derives `probability` from the stage.
    return updated;
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
                    onOpen={setSelectedDealId}
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

      <CollectionDetail<CrmDeal>
        config={DEAL_DETAIL_CONFIG}
        // Nothing on this page is a deal ROW — every entry is a per-rep touch summary — so the
        // canonical array is empty and every open resolves through `loadById`.
        items={EMPTY_DEALS}
        selectedId={selectedDealId}
        onSelect={id => {
          if (id === null) { setSelectedDealId(null); reload(); }
          else setSelectedDealId(Number(id));
        }}
        // Nothing to navigate: this list is grouped by rep and capped per rep, so ‹ › would walk
        // a set the user did not open from. `[]` is that answer, as on the dashboard.
        navOrder={EMPTY_NAV}
        detail={{
          render: (deal, ctx) => (
            <DealDetailBody
              deal={deal}
              // Always false: see `items` above.
              onBoard={false}
              // No board for a deal to be off, so the close-out actions are on offer. An ARCHIVED
              // deal is still gated — by the body's own detail fetch, the only thing here that
              // knows.
              stageWritable
              ctx={ctx}
              onMarkWon={d => updateDealStage(d, 'won')}
              onMarkLost={(d, lostReason) => updateDealStage(d, 'lost', lostReason)}
              onSaveDeal={saveDeal}
              onRestored={restored => {
                setSelectedDealId(prev => (prev === restored.id ? null : prev));
                reload();
              }}
            />
          ),
          onRequestClose: denyEscapeBackdrop,
        }}
      />
    </Frame>
  );
}
