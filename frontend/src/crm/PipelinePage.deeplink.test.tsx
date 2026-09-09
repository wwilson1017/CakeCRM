// @vitest-environment jsdom
//
// Deal deep links (issue #145), carried across #74's rewrite of the board onto the shared
// collection layer.
//
// It lives in its own file for the same hard reason `PipelinePage.archived.test.tsx` does:
// `vi.mock` is file-scoped, and `PipelinePage.test.tsx` mocks `DealDetailBody` (its subject
// is whether the page routes a selection to the sheet, not what the sheet renders), while
// every assertion here needs the REAL sheet — "the link opened the deal" is exactly the
// claim that a mocked sheet cannot make. The harness below is the archived file's, which is
// already adapted to the rewritten board; the deep-link helpers main kept in its combined
// file are restored beneath it.
//
// Resolution happens during render (this repo's react-hooks ruleset makes a synchronous
// setState inside an effect a build error), so "does it settle instead of looping?" is a
// real question about this code and not a hypothetical — every test here would time out
// rather than fail if it did not.
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { CrmDeal } from '../core/types';

const api = vi.hoisted(() => vi.fn());
// `importOriginal` rather than a hand-written stub: PipelinePage imports `ApiError` from
// this module too (it reads `.status` off a thrown bulk-move error), and a second class
// with the same name would break `instanceof` in a way no assertion here would notice.
vi.mock('../core/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../core/api/client')>()),
  api,
}));
const toast = vi.hoisted(() => ({ error: vi.fn(), info: vi.fn(), success: vi.fn() }));
vi.mock('../shared/toast', () => ({ toast }));

// The one gesture jsdom cannot produce. `KanbanBoard` recognises a drag through dnd-kit —
// pointer capture, ResizeObserver, live layout rects — none of which exist here, and there
// is no keyboard sensor to fall back on (`shared/dnd/sensors.ts` registers Pointer + Touch
// only). It matters because DRAG IS THE ONLY WRITE ON THIS PAGE THAT DOES NOT ALSO SCHEDULE
// A REFRESH: `updateDealStage` and `applyBulkMove` both call `load` themselves, so the
// write-completes-inside-a-GET race below is unreachable through any other control.
//
// So the real board renders UNCHANGED and one extra button is added beside it, calling the
// very prop dnd-kit calls — `onMove`, with a real `MoveEvent`. Nothing of PipelinePage is
// stubbed; only the gesture recogniser is bypassed. It deliberately does NOT consult
// `dragDisabled`, so it can never be used to claim a card IS draggable — the drag POLICY is
// pinned in `shared/dnd/dragDisabled.test.ts`, and the one test that presses this button
// drags a live deal that is allowed to move.
//
// Since #74 the board sits under the collection layer, which hands `shared/dnd` items
// WRAPPED as `{id, item}` and unwraps them again in `onMove`. Finding by `.id` and handing
// the wrapper straight back therefore still works — and must, since `KanbanView` is what
// does the unwrapping.
const dragIntent = vi.hoisted(() => ({ current: null as { id: number; to: string } | null }));
vi.mock('../shared/dnd', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../shared/dnd')>();
  const RealBoard = actual.KanbanBoard;
  function DraggableBoard(props: Parameters<typeof RealBoard>[0]) {
    const fire = () => {
      const intent = dragIntent.current;
      if (!intent) throw new Error('drag fired with no dragIntent set');
      for (const [columnId, items] of Object.entries(props.items)) {
        const item = items.find(i => i.id === intent.id);
        if (item) {
          void props.onMove({ item, fromColumnId: columnId, toColumnId: intent.to, newIndex: 0 });
          return;
        }
      }
      throw new Error(`drag fired for deal ${intent.id}, which is not on the board`);
    };
    return (
      <>
        <button aria-label="fire drag" onClick={fire}>fire drag</button>
        <RealBoard {...props} />
      </>
    );
  }
  return { ...actual, KanbanBoard: DraggableBoard };
});

const { PipelinePage } = await import('./PipelinePage');
const { ActiveRecordProvider } = await import('./RecordContext');
// PipelinePage reads `?stage=` through useSearchParams, and the deal sheet publishes the
// open record — both are ambient app scaffolding the test supplies rather than the
// component being reshaped to avoid them.
const { MemoryRouter, useLocation, useNavigate } = await import('react-router-dom');

function deal(over: Partial<CrmDeal> = {}): CrmDeal {
  return {
    id: 1, title: 'Untitled', stage: 'lead', value: 0, probability: 20,
    expected_close_date: '', notes: '', contact_id: null, company_id: null, currency: 'USD',
    archived_at: null,
    created_at: '2026-08-01T00:00:00+00:00', updated_at: '2026-08-01T00:00:00+00:00',
    ...over,
  };
}

/** One live deal and one archived deal, same open stage — the pair the money assertion
 *  needs: both on the board, only one of them pipeline. */
const LIVE = deal({ id: 1, title: 'Acme renewal', stage: 'lead', value: 100 });
const ARCHIVED = deal({
  id: 2, title: 'Zebra rebuild', stage: 'lead', value: 99_999,
  archived_at: '2026-08-20T00:00:00+00:00',
});

// #59: the board sweeps keyset pages instead of issuing one bare GET. Every fixture here
// is well under one page, so each load is exactly one request — at these page-0 URLs.
const LIVE_PATH = '/api/crm/deals?sort=id&limit=501';
const ARCHIVED_PATH = '/api/crm/deals?sort=id&limit=501&include_archived=true';

interface RouteOptions {
  /** Board payload for the plain (live-only) request the server answers unasked. */
  live?: CrmDeal[];
  /** Board payload for `?include_archived=true`. */
  withArchived?: CrmDeal[];
  /** Per-request override; return `undefined` to fall through to the defaults. The request
   *  init comes through because one path serves two verbs — the detail sheet GETs
   *  `/api/crm/deals/:id` and a stage move PUTs it — and only the PUT is ever held. */
  over?: (path: string, init?: RequestInit) => unknown;
}

/** Deals the fake server has restored during this test. `POST /restore` is the only write
 *  these tests make that the server REMEMBERS, and a restore now fires a follow-up board
 *  GET — so a fixture that kept answering "archived" would be asserting its own opinion
 *  over the component's behaviour. Reset per test. */
const restoredIds = new Set<number>();

/** A board row as the server would now serve it. */
function asServed(d: CrmDeal): CrmDeal {
  return restoredIds.has(d.id) ? { ...d, archived_at: null } : d;
}

/** What `POST /restore` answers: `get_deal`'s projection — the row, live, WITHOUT the
 *  board-only fields `get_pipeline` derives. Narrower than a board row on purpose; that
 *  difference is the entire subject of the MERGE test. */
function detailProjection(d: CrmDeal): CrmDeal {
  const row: CrmDeal = { ...d, archived_at: null };
  delete row.last_activity_at;
  return row;
}

/** Route the whole component tree's fetches. The two board paths answer DIFFERENT payloads
 *  on purpose — that is the server contract the facet exists to reach, and routing them
 *  identically would make the fetch-widening assertion meaningless. */
function routeApi(opts: RouteOptions = {}) {
  const live = opts.live ?? [LIVE];
  const withArchived = opts.withArchived ?? [LIVE, ARCHIVED];
  api.mockImplementation(async (path: string, init?: RequestInit) => {
    const custom = opts.over?.(path, init);
    if (custom !== undefined) return custom;
    const restoring = /^\/api\/crm\/deals\/(\d+)\/restore$/.exec(path);
    if (restoring) {
      const id = Number(restoring[1]);
      const row = [...withArchived, ...live].find(d => d.id === id);
      if (!row) throw new Error(`restore called for deal ${id}, which no payload contains`);
      restoredIds.add(id);
      return detailProjection(row);
    }
    if (path === LIVE_PATH) return { deals: live.map(asServed) };
    if (path === ARCHIVED_PATH) return { deals: withArchived.map(asServed) };
    if (path === '/api/users') return { users: [] };
    if (path === `/api/crm/deals/${ARCHIVED.id}`) return ARCHIVED;
    if (path === `/api/crm/deals/${LIVE.id}`) return LIVE;
    if (path.includes('/provenance')) return { provenance: [] };
    if (path.includes('/chatter/')) return { notes: [] };
    if (path.includes('/fields')) return [];
    if (path.includes('/touch-count/')) return null;
    return null;
  });
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  // jsdom implements no CSS media queries at all, so `window.matchMedia` is simply absent
  // and `useIsMobile` throws on mount. Stub the desktop answer — this is a missing jsdom
  // API, not a shim around our own code. Desktop matters: bulk checkboxes are hidden on
  // mobile, so a mobile stub would make every "inert" assertion pass vacuously.
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  // jsdom implements no layout, so Element.scrollIntoView does not exist — the deep-link
  // and chip-bar paths both call it.
  Element.prototype.scrollIntoView = vi.fn();
  // The layer persists its filter envelope, sort and view to sessionStorage, so a facet
  // left on by one test would silently arm the next one.
  sessionStorage.clear();
  restoredIds.clear();
  api.mockReset();
  toast.error.mockReset();
  toast.info.mockReset();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render(url = '/crm/pipeline') {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[url]}>
        <ActiveRecordProvider><PipelinePage /></ActiveRecordProvider>
      </MemoryRouter>,
    );
  });
  // `load` is dispatched through queueMicrotask, and the board only renders once its
  // payload has resolved through it — drain that chain before asserting.
  await flush();
}

async function flush() {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

async function click(el: Element | null | undefined, what: string) {
  if (!el) throw new Error(`nothing to click: ${what}`);
  await act(async () => { (el as HTMLElement).click(); });
  await flush();
}

/** Find a button by its visible text OR its `aria-label`. Since #75 the detail panel is the
 *  collection layer's shell, whose Close is an icon button carrying only the accessible name —
 *  so matching on text alone finds nothing where the deleted sheet had a worded button. */
function button(label: string): HTMLButtonElement | undefined {
  return [...container.querySelectorAll('button')]
    .find(b => b.textContent?.trim() === label || b.getAttribute('aria-label') === label) as
      HTMLButtonElement | undefined;
}

/** Open the collection toolbar's facet panel if it is not already open. Every facet moved
 *  behind this disclosure with #74 — the bespoke bar's per-facet popovers are gone — so the
 *  three helpers below share one way in. The trigger's label carries the active count, so it
 *  is matched by prefix rather than exact text. */
async function openFacetPanel() {
  const trigger = [...container.querySelectorAll('button')]
    .find(b => (b.textContent ?? '').trim().startsWith('Filters')) as HTMLButtonElement | undefined;
  if (!trigger) throw new Error('no Filters disclosure on the toolbar');
  if (trigger.getAttribute('aria-expanded') === 'true') return;
  await click(trigger, 'Filters');
}

/** Pick one of a facet's options. The layer renders every option as a chip button labelled
 *  with the option text, so the option label alone identifies it. */
async function pickFacetOption(option: string) {
  await openFacetPanel();
  await click(button(option), option);
}

/** Turn the Archived facet on. Same two states the retired filter bar offered.
 *
 *  On an EMPTY board the toolbar is not the way in, and that is not a test workaround — it is
 *  the behaviour the page-header link exists for. `CollectionView` answers an empty `items`
 *  with its own empty state rendered INSTEAD OF the toolbar, so on a board with no live deals
 *  the facet the retired filter bar always showed would be unreachable exactly when it is
 *  needed: archive your last open deal and the recovery view disappears with it. The header
 *  link is the undo, mounted above the layer for the same reason "Show all" is. */
async function pickArchivedFacet(option: 'Include archived' | 'Archived only') {
  const headerLink = button('Show archived deals');
  if (headerLink) {
    await click(headerLink, 'Show archived deals');
    // The header link can only ask for 'only' — it is the recovery view. A test that wants
    // 'include' from an empty board would be asking for a control that does not exist.
    if (option === 'Archived only') return;
  }
  await pickFacetOption(option);
}


/** Open the Deal-activity facet and pick one of its buckets. */
async function pickActivityFacet(option: 'No activity logged') {
  await pickFacetOption(option);
}

/** Open a card's detail sheet and close it again — the reachable way to fire a SILENT board
 *  refresh, which is how a fresh payload lands without a facet change. */
async function reopenAndClose(title: string) {
  await click(card(title), `${title} card`);
  await click(button('Close'), 'Close');
}

/** Complete a stage drag the way dnd-kit does — by calling the board's `onMove`. See the
 *  `shared/dnd` mock at the top for why this is a button press and not a gesture. */
async function fireDrag(dealId: number, toStage: string) {
  dragIntent.current = { id: dealId, to: toStage };
  await click(container.querySelector('button[aria-label="fire drag"]'), 'drag');
}

/** A promise whose resolution the test controls, so one board GET can be pinned in flight
 *  across other interactions and landed afterwards. Ordering is the whole subject of the
 *  in-flight-load test below, and `await`ing the mock cannot express it. */
function deferred<T>() {
  let settle!: (value: T) => void;
  let fail!: (reason: unknown) => void;
  const promise = new Promise<T>((resolve, reject) => { settle = resolve; fail = reject; });
  // The rejection path matters as much as the resolution one: a request that FAILS
  // instantly is over before any assertion can look at what the page did while it was in
  // flight, which is exactly how a mid-flight guard goes quietly vacuous.
  promise.catch(() => {}); // pre-attach, so holding an unrejected promise is never "unhandled"
  return { promise, resolve: settle, reject: fail };
}



function card(title: string): HTMLElement | undefined {
  return [...container.querySelectorAll('[role="button"]')]
    .find(el => el.textContent?.includes(title)) as HTMLElement | undefined;
}

/** A stage column, or undefined when that stage is not on the board at all. Deliberately
 *  NOT the archived file's throwing variant: "there is no `won` column" is a precondition
 *  the hidden-stage test below asserts, not a broken fixture. */
function stageColumn(stage: string): HTMLElement | undefined {
  return (container.querySelector(`[data-stage="${stage}"]`) ?? undefined) as
    HTMLElement | undefined;
}



/** Every BOARD request made so far, in order. Child components fetch too, so filtering to
 *  the two board paths is what makes "the facet drives the fetch" a statement about the
 *  board rather than about traffic in general. */
function boardRequests(): string[] {
  return api.mock.calls
    .map(c => String(c[0]))
    .filter(p => p === LIVE_PATH || p === ARCHIVED_PATH);
}

/** Navigate a MOUNTED page to a new URL — the deep-link case that matters most, since the
 *  assistant's drawer is a slide-over: clicking a link it produced changes the search
 *  params of a page that is already showing a board, without remounting anything. A plain
 *  `render()` cannot express that; it would always look like a cold load. */
function Navigator({ to }: { to: string | null }) {
  const navigate = useNavigate();
  useEffect(() => { if (to) navigate(to); }, [navigate, to]);
  return null;
}

/** The router's live search string. `window.location` is useless under MemoryRouter, which
 *  keeps its history in memory — an assertion against it passes whatever the page does. */
const seenSearch = { current: '' };

function LocationProbe() {
  const loc = useLocation();
  // In an effect, not during render: this repo's react-hooks ruleset forbids writing to a
  // value defined outside the component while rendering.
  useEffect(() => { seenSearch.current = loc.search; }, [loc.search]);
  return null;
}

async function renderWithLocationProbe(url: string) {
  seenSearch.current = '';
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[url]}>
        <ActiveRecordProvider><PipelinePage /><LocationProbe /></ActiveRecordProvider>
      </MemoryRouter>,
    );
  });
  await flush();
  await flush();
}

async function renderThenNavigate(to: string) {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={['/crm/pipeline']}>
        <ActiveRecordProvider><PipelinePage /></ActiveRecordProvider>
      </MemoryRouter>,
    );
  });
  await flush();
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={['/crm/pipeline']}>
        <ActiveRecordProvider><PipelinePage /><Navigator to={to} /></ActiveRecordProvider>
      </MemoryRouter>,
    );
  });
  await flush();
  await flush();
}

// ── Deal deep links (issue #145) ────────────────────────────────────────────────────
//
// The assistant attaches `/crm/pipeline?deal=N` to every deal it names, including in
// Telegram messages and notifications that leave the app entirely. A link that silently
// does nothing is the dead end #145 was filed to close — and a link that WRONGLY says the
// deal was deleted is worse than the dead end, which is what the refresh below is for.
//
// Resolution happens during render (this repo's react-hooks ruleset makes a synchronous
// setState inside an effect a build error), so "does it settle instead of looping?" is a
// real question about this code and not a hypothetical — every test here would time out
// rather than fail if it did not.

/** The dead-link notice's text, or null when it is not on screen. */
function deadLinkNotice(): string | null {
  const el = [...container.querySelectorAll('span')]
    .find(n => n.textContent?.includes("isn't on this board"));
  return el?.textContent?.replace(/\s+/g, ' ').trim() ?? null;
}

describe('PipelinePage — deal deep links', () => {
  it('opens the named deal on a cold load', async () => {
    routeApi();
    await render(`/crm/pipeline?deal=${LIVE.id}`);

    // The detail sheet is open on that deal, not merely scrolled to its card.
    expect(button('Close')).toBeTruthy();
    expect(container.textContent).toContain('Acme renewal');
    expect(deadLinkNotice()).toBeNull();
  });

  it('says nothing at all about a deal id that is not a deal id', async () => {
    routeApi();
    await render('/crm/pipeline?deal=abc');

    // A malformed link is not a deleted deal. Accusing anyone of deleting "abc" would be
    // the same wrong answer the notice exists to avoid, just with worse wording.
    expect(deadLinkNotice()).toBeNull();
    expect(button('Close')).toBeFalsy();
  });

  it('warns that a deal missing from the board may be archived or deleted', async () => {
    routeApi();
    await render('/crm/pipeline?deal=404');

    const notice = deadLinkNotice();
    expect(notice).toContain('#404');
    expect(notice).toContain('archived or deleted');
  });

  it('opens an archived deal from a link when the Archived facet is showing it', async () => {
    // The notice tells the user to turn the facet on, so following that advice has to
    // work: with archived rows in the payload the link resolves like any other.
    routeApi();
    await render(`/crm/pipeline?deal=${ARCHIVED.id}`);
    expect(deadLinkNotice()).toContain(`#${ARCHIVED.id}`);

    await pickArchivedFacet('Include archived');
    expect(deadLinkNotice()).toBeNull();
    expect(button('Close')).toBeTruthy();
    expect(container.textContent).toContain('Zebra rebuild');
  });

  it('refreshes before accusing when the board on screen predates the link', async () => {
    // THE false-accusation case, and the most reachable one: the assistant creates a deal
    // and hands back its link while its drawer sits over an already-loaded board. That
    // board is silent about the new deal, not evidence against it.
    const NEW_DEAL = deal({ id: 77, title: 'Fresh signing', stage: 'lead', value: 500 });
    let served: CrmDeal[] = [LIVE];
    routeApi({ over: (path) => (path === LIVE_PATH ? { deals: served } : undefined) });

    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={['/crm/pipeline']}>
          <ActiveRecordProvider><PipelinePage /></ActiveRecordProvider>
        </MemoryRouter>,
      );
    });
    await flush();
    expect(boardRequests()).toHaveLength(1);
    expect(card('Fresh signing')).toBeFalsy();

    // The deal is created behind the board's back, then the link arrives.
    served = [LIVE, NEW_DEAL];
    await renderThenNavigate('/crm/pipeline?deal=77');

    // It refetched rather than declaring the deal gone, and then opened it.
    expect(deadLinkNotice()).toBeNull();
    expect(container.textContent).toContain('Fresh signing');
  });

  it('refreshes SILENTLY, which is what proves the page was never remounted', async () => {
    // The anti-vacuity test for every "already-loaded board" case in this block. A remount
    // would reload from scratch and find whatever the server now says — visibly identical
    // to a stale-board refresh, and it would make those tests assert nothing. The two are
    // told apart by HOW the load is taken: a mount load is non-silent, and `loading`
    // returns the spinner INSTEAD OF the board, so the board would be gone from the DOM.
    const held = deferred<{ deals: CrmDeal[] }>();
    let boardCalls = 0;
    routeApi({
      over: (path) => {
        if (path !== LIVE_PATH) return undefined;
        boardCalls += 1;
        return boardCalls === 1 ? { deals: [LIVE] } : held.promise;
      },
    });

    await renderThenNavigate('/crm/pipeline?deal=404');

    expect(boardCalls).toBe(2);                  // the link really did trigger a refetch
    expect(card('Acme renewal')).toBeTruthy();   // ...and the board never left the screen
    expect(deadLinkNotice()).toBeNull();         // ...and it accused nobody while waiting

    await act(async () => { held.resolve({ deals: [LIVE] }); });
    await flush();
    expect(deadLinkNotice()).toContain('#404');
  });

  it('is not fooled into accusing by an optimistic update landing mid-refresh', async () => {
    // Found in review, and it was a real false accusation. "Has the board caught up?" used
    // to be `data !== <the object on screen when the link arrived>` — but `data`'s identity
    // is bumped by every optimistic update on this page (a drag's stage patch, its
    // rollback, the same-column reorder, a bulk reconcile), none of which asked the server
    // anything. Dragging an unrelated card while the deep link's own refresh was still in
    // flight therefore read as "the server has spoken" and the page declared a live,
    // just-created deal archived or deleted. Dragging is the most routine gesture on this
    // page, so this was reachable constantly.
    const NEW_DEAL = deal({ id: 77, title: 'Fresh signing', stage: 'lead', value: 500 });
    const held = deferred<{ deals: CrmDeal[] }>();
    let boardCalls = 0;
    routeApi({
      over: (path, init) => {
        if (init?.method === 'PUT') return { ...LIVE, stage: 'lead' };
        if (path !== LIVE_PATH) return undefined;
        boardCalls += 1;
        return boardCalls === 1 ? { deals: [LIVE] } : held.promise;
      },
    });

    await renderThenNavigate('/crm/pipeline?deal=77');
    expect(boardCalls).toBe(2);          // the catch-up refresh is in flight...
    expect(deadLinkNotice()).toBeNull(); // ...and nothing has been decided yet

    // A same-column drop: pure optimistic bookkeeping, no server answer about deal 77.
    await fireDrag(LIVE.id, 'lead');
    expect(deadLinkNotice()).toBeNull();

    // Only the real payload may settle it — and here it does, correctly.
    await act(async () => { held.resolve({ deals: [LIVE, NEW_DEAL] }); });
    await flush();
    expect(deadLinkNotice()).toBeNull();
    expect(container.textContent).toContain('Fresh signing');
  });

  it('asks for fresh data exactly once, then accuses only if the deal is still missing', async () => {
    // The other half of the same rule: the refresh must be bounded, or a genuinely deleted
    // deal would refetch the board forever instead of saying so.
    routeApi();
    await renderThenNavigate('/crm/pipeline?deal=404');
    const afterFirst = boardRequests().length;

    expect(deadLinkNotice()).toContain('#404');
    // Settle repeatedly: a render-phase resolution that failed to converge would keep
    // firing loads here rather than sitting still.
    await flush();
    await flush();
    expect(boardRequests()).toHaveLength(afterFirst);
  });

  it('says nothing when the refresh it asked for fails', async () => {
    // A failed load applies no payload, so the board never becomes authoritative about the
    // link. Saying nothing loses a correct notice about a genuinely deleted deal; saying
    // "archived or deleted" would tell the user their live deal was gone because a request
    // failed. The trade is deliberate.
    let boardCalls = 0;
    routeApi({
      over: (path) => {
        if (path !== LIVE_PATH) return undefined;
        boardCalls += 1;
        return boardCalls === 1 ? { deals: [LIVE] } : Promise.reject(new Error('network'));
      },
    });

    await renderThenNavigate('/crm/pipeline?deal=404');
    await flush();

    expect(boardCalls).toBeGreaterThan(1);   // it really did try
    expect(deadLinkNotice()).toBeNull();     // and it really did stay quiet
  });

  it('ignores a load that was already in flight when the link arrived', async () => {
    // A load that STARTED before the link cannot know about a deal created after it, so its
    // landing must not settle the link. This pins the OBSERVABLE guarantee — no false
    // notice — through the nastiest arrangement reachable from the page's own controls: a
    // board refresh in flight, a stage write pending (so the link's own refresh is deferred
    // rather than sent), and the older payload landing into that.
    //
    // Honest about what it isolates: three guards hold here at once — `load`'s newest-wins
    // `loadGen` check, its pending-write defer, and the generation comparison in the
    // deep-link resolution — and this test cannot tell which one saved it. Removing the
    // generation comparison alone still passes. It is kept as defence in depth, and this
    // test is kept because the BEHAVIOUR is what must never regress, whichever guard is
    // carrying it.
    const NEW_DEAL = deal({ id: 77, title: 'Fresh signing', stage: 'lead', value: 500 });
    const stale = deferred<{ deals: CrmDeal[] }>();
    const write = deferred<CrmDeal>();
    let boardCalls = 0;
    routeApi({
      over: (path, init) => {
        if (init?.method === 'PUT') return write.promise;
        if (path !== LIVE_PATH) return undefined;
        boardCalls += 1;
        if (boardCalls === 1) return { deals: [LIVE] };
        if (boardCalls === 2) return stale.promise;   // started BEFORE the link
        return { deals: [LIVE, NEW_DEAL] };
      },
    });

    // A board refresh goes out (open and close a card) and is held...
    await render('/crm/pipeline');
    await click(card('Acme renewal'), 'card');
    await click(button('Close'), 'Close');
    expect(boardCalls).toBe(2);

    // ...then a drag starts, so any further load defers rather than clobbering it.
    await fireDrag(LIVE.id, 'won');

    await renderThenNavigate('/crm/pipeline?deal=77');
    expect(boardCalls).toBe(2);   // the link's own refresh was deferred, not sent

    // So the OLDER load is the only one that lands. It knows nothing about deal 77 and
    // must not be read as the server having answered about it.
    await act(async () => { stale.resolve({ deals: [LIVE] }); });
    await flush();
    expect(deadLinkNotice()).toBeNull();

    // The write settles, the deferred refresh replays, and THAT one settles the link.
    await act(async () => { write.resolve({ ...LIVE, stage: 'won' }); });
    await flush();
    await flush();
    expect(deadLinkNotice()).toBeNull();
    expect(container.textContent).toContain('Fresh signing');
  });

  it('follows the same link again after the first visit is closed', async () => {
    // Resolution keys off the NAVIGATION, which is what makes this work: with the id alone,
    // a second click on the same link changed nothing, so after closing the sheet that link
    // was dead for the rest of the session — and a chat transcript is exactly where the same
    // link gets clicked twice.
    routeApi();
    await render(`/crm/pipeline?deal=${LIVE.id}`);
    expect(button('Close')).toBeTruthy();
    await click(button('Close'), 'Close');
    expect(button('Close')).toBeFalsy();

    await renderThenNavigate(`/crm/pipeline?deal=${LIVE.id}`);
    expect(button('Close')).toBeTruthy();
  });

  it('replaces the previous link\'s sheet when a newer link resolves dead', async () => {
    // Otherwise the old deal's sheet sits there through the new link's refresh and after its
    // verdict, reading as though the new link had opened the wrong record.
    routeApi();
    await render(`/crm/pipeline?deal=${LIVE.id}`);
    expect(container.textContent).toContain('Acme renewal');
    expect(button('Close')).toBeTruthy();

    await renderThenNavigate('/crm/pipeline?deal=404');
    expect(button('Close')).toBeFalsy();
    expect(deadLinkNotice()).toContain('#404');
  });

  it('takes back the notice when the user follows its advice and the deal appears', async () => {
    // The notice tells the user to turn on the Archived filter. Doing so refetches and the
    // deal arrives — at which point the notice's claim is false, and opening the deal is
    // what following the link asked for in the first place.
    routeApi();
    await render(`/crm/pipeline?deal=${ARCHIVED.id}`);
    expect(deadLinkNotice()).toContain(`#${ARCHIVED.id}`);

    await pickArchivedFacet('Include archived');
    expect(deadLinkNotice()).toBeNull();
    expect(button('Close')).toBeTruthy();
    expect(container.textContent).toContain('Zebra rebuild');
  });

  it('resolves a second link that arrives while the first is still refreshing', async () => {
    // Both links sit at the `refresh` verdict, so an effect keyed only on the verdict never
    // re-runs for the second one — which would then ride the first link's request, whose
    // generation predates it and therefore can never settle it. The second link would hang
    // unresolved forever.
    const DEAL_B = deal({ id: 88, title: 'Second signing', stage: 'lead', value: 900 });
    const first = deferred<{ deals: CrmDeal[] }>();
    let boardCalls = 0;
    routeApi({
      over: (path) => {
        if (path !== LIVE_PATH) return undefined;
        boardCalls += 1;
        if (boardCalls === 1) return { deals: [LIVE] };
        if (boardCalls === 2) return first.promise;   // link A's refresh, held
        return { deals: [LIVE, DEAL_B] };             // link B's own refresh
      },
    });

    await render('/crm/pipeline');
    await renderThenNavigate('/crm/pipeline?deal=77');
    expect(boardCalls).toBe(2);   // A asked, and is waiting

    await renderThenNavigate('/crm/pipeline?deal=88');
    expect(boardCalls).toBe(3);   // B asked for its OWN load rather than riding A's
    expect(container.textContent).toContain('Second signing');
    expect(deadLinkNotice()).toBeNull();

    // A's stale answer arriving late must not now accuse B.
    await act(async () => { first.resolve({ deals: [LIVE] }); });
    await flush();
    expect(deadLinkNotice()).toBeNull();
  });

  it('retries the same link after its refresh failed', async () => {
    // A failed refresh resolves nothing, so the parameter is never consumed — which used to
    // make the retry indistinguishable from no click at all, and the link dead for the rest
    // of the session precisely when the user has most reason to try it again.
    const NEW_DEAL = deal({ id: 77, title: 'Fresh signing', stage: 'lead', value: 500 });
    let boardCalls = 0;
    routeApi({
      over: (path) => {
        if (path !== LIVE_PATH) return undefined;
        boardCalls += 1;
        if (boardCalls === 1) return { deals: [LIVE] };
        if (boardCalls === 2) return Promise.reject(new Error('network'));
        return { deals: [LIVE, NEW_DEAL] };
      },
    });

    await render('/crm/pipeline');
    await renderThenNavigate('/crm/pipeline?deal=77');
    await flush();
    expect(boardCalls).toBe(2);
    expect(deadLinkNotice()).toBeNull();     // it stayed quiet rather than accusing

    // The same link, clicked again once the network is back.
    await renderThenNavigate('/crm/pipeline?deal=77');
    await flush();
    expect(boardCalls).toBe(3);
    expect(container.textContent).toContain('Fresh signing');
  });

  it('leaves a sheet the user opened by hand alone when a later link resolves dead', async () => {
    // A link once opened deal 1; the user then closed it and opened that same card
    // themselves. Tracking only "which id a link opened" would close their sheet on the next
    // link — the sheet has to remember that the USER put it there.
    routeApi();
    await render(`/crm/pipeline?deal=${LIVE.id}`);
    await click(button('Close'), 'Close');
    await click(card('Acme renewal'), 'card opened by hand');
    expect(button('Close')).toBeTruthy();

    await renderThenNavigate('/crm/pipeline?deal=404');
    expect(button('Close')).toBeTruthy();          // still theirs
    expect(deadLinkNotice()).toContain('#404');    // and the new link still reports
  });

  it('releases link ownership when the user re-opens that same deal by hand', async () => {
    // The narrow case the hand-open clear exists for, and the one the test above cannot
    // reach: that one CLOSES the sheet first, and closing already releases ownership, so it
    // passes with or without the clear. Here the sheet is never closed — the user walks it
    // from the linked deal to another card and back — which leaves `selectedDealId` equal to
    // the id the link opened while the sheet is now the USER's. Tracking the id alone would
    // then close their sheet on the next link, which is exactly what #145 documents as wrong.
    const OTHER = deal({ id: 7, title: 'Globex expansion', stage: 'lead', value: 250 });
    routeApi({ live: [LIVE, OTHER] });
    await render(`/crm/pipeline?deal=${LIVE.id}`);
    expect(button('Close')).toBeTruthy();

    // Walk the sheet away from the linked deal and back, without ever closing it.
    await click(card('Globex expansion'), 'other card opened by hand');
    await click(card('Acme renewal'), 'linked deal re-opened by hand');

    await renderThenNavigate('/crm/pipeline?deal=404');
    expect(button('Close')).toBeTruthy();          // still theirs
    expect(deadLinkNotice()).toContain('#404');    // and the new link still reports
  });

  it('keeps the deal in the URL, and lets ?stage= be consumed beside it', async () => {
    // The parameter is deliberately kept, so a reload reopens the deal and the address bar
    // is a real copy source. ?stage= beside it is still a one-shot intent and is consumed —
    // the two must not interfere, which they would if both rewrote the same params object.
    //
    // Asserted through the ROUTER's location, not `window.location`: MemoryRouter keeps its
    // history in memory and never touches the document URL, so reading `window.location`
    // here would pass no matter what the page did.
    routeApi();
    await renderWithLocationProbe(`/crm/pipeline?stage=lead&deal=${LIVE.id}`);

    expect(button('Close')).toBeTruthy();
    const params = new URLSearchParams(seenSearch.current);
    expect(params.get('deal')).toBe(String(LIVE.id));
    expect(params.get('stage')).toBeNull();
  });

  it('retires a link whose refresh failed instead of leaving it armed', async () => {
    // A failed refresh leaves the verdict at `refresh` and the effect does not run again.
    // Left armed, the link would sit there until some unrelated load minutes later — closing
    // another card's sheet, a facet flip — happened to satisfy it, and a sheet would open
    // with no gesture toward it. Retiring keeps the page silent, which is the documented
    // trade, and following the link again still retries.
    const NEW_DEAL = deal({ id: 77, title: 'Fresh signing', stage: 'lead', value: 500 });
    let boardCalls = 0;
    routeApi({
      over: (path) => {
        if (path !== LIVE_PATH) return undefined;
        boardCalls += 1;
        if (boardCalls === 1) return { deals: [LIVE] };
        if (boardCalls === 2) return Promise.reject(new Error('network'));
        return { deals: [LIVE, NEW_DEAL] };
      },
    });

    await render('/crm/pipeline');
    await renderThenNavigate('/crm/pipeline?deal=77');
    await flush();
    expect(boardCalls).toBe(2);
    expect(deadLinkNotice()).toBeNull();

    // An unrelated later load succeeds and brings the deal in. The link is retired, so
    // nothing opens by itself.
    await reopenAndClose('Acme renewal');
    await flush();
    expect(boardCalls).toBe(3);
    expect(card('Fresh signing')).toBeTruthy();   // it really is on the board now
    expect(button('Close')).toBeFalsy();          // ...and no sheet opened on its own
  });

  it('drops the notice when the user navigates away from the link', async () => {
    // Found by the independent verifier. Clicking the app's own Pipeline nav item from
    // `/crm/pipeline?deal=404` keeps this page mounted, so a notice about a link that is no
    // longer in the URL used to sit there until dismissed by hand.
    routeApi();
    await render('/crm/pipeline?deal=404');
    expect(deadLinkNotice()).toContain('#404');

    await renderThenNavigate('/crm/pipeline');
    expect(deadLinkNotice()).toBeNull();
  });

  it('does not retire a link whose refresh was merely superseded', async () => {
    // Found by the PR reviewer. `load` returns false for three different reasons, and only
    // one of them is a failure: it also returns false when DEFERRED behind a write and when
    // SUPERSEDED by a newer load. Following the same link again starts a newer load and
    // supersedes the first — retiring on that marked the SECOND navigation handled, so its
    // successful payload arrived and opened nothing at all.
    // ORDER IS THE WHOLE TEST: the superseded attempt has to settle BEFORE the newer
    // payload arrives. If the newer one lands first it has already opened the deal, and a
    // late retirement changes nothing visible — which is exactly how a first cut of this
    // test passed against the bug.
    const NEW_DEAL = deal({ id: 77, title: 'Fresh signing', stage: 'lead', value: 500 });
    const firstAttempt = deferred<{ deals: CrmDeal[] }>();
    const secondAttempt = deferred<{ deals: CrmDeal[] }>();
    let boardCalls = 0;
    routeApi({
      over: (path) => {
        if (path !== LIVE_PATH) return undefined;
        boardCalls += 1;
        if (boardCalls === 1) return { deals: [LIVE] };
        if (boardCalls === 2) return firstAttempt.promise;    // held, then superseded
        return secondAttempt.promise;                         // held, the real answer
      },
    });

    await render('/crm/pipeline');
    await renderThenNavigate('/crm/pipeline?deal=77');
    expect(boardCalls).toBe(2);

    // The same link again: a newer navigation starting a newer load, which supersedes the
    // first — `load` will hand the first one back `applied === false`.
    await renderThenNavigate('/crm/pipeline?deal=77');
    expect(boardCalls).toBe(3);

    // The superseded attempt settles FIRST. Retiring on it marks the second navigation
    // handled, and its answer below then opens nothing at all.
    await act(async () => { firstAttempt.resolve({ deals: [LIVE] }); });
    await flush();
    expect(deadLinkNotice()).toBeNull();

    // The second navigation's own answer, carrying the deal.
    await act(async () => { secondAttempt.resolve({ deals: [LIVE, NEW_DEAL] }); });
    await flush();

    expect(container.textContent).toContain('Fresh signing');
    expect(button('Close')).toBeTruthy();
    expect(deadLinkNotice()).toBeNull();
  });

  it('lets the user dismiss the notice, and does not re-raise it on its own', async () => {
    routeApi();
    await render('/crm/pipeline?deal=404');
    expect(deadLinkNotice()).toBeTruthy();

    await click(button('Dismiss'), 'Dismiss');
    expect(deadLinkNotice()).toBeNull();

    // A board refresh is the event most likely to re-raise a dismissed notice, since it is
    // what re-runs the whole resolution. Dismissal is per-target, so it must survive one.
    await reopenAndClose('Acme renewal');
    expect(deadLinkNotice()).toBeNull();
  });

  it('does not reopen the sheet after the user closes it, though the link is still in the URL', async () => {
    // The parameter is deliberately kept — it makes reload reopen the deal and the address
    // bar a real copy source — so "resolve once per target" has to be what stops the sheet
    // from springing back the moment it is closed.
    routeApi();
    await render(`/crm/pipeline?deal=${LIVE.id}`);
    expect(button('Close')).toBeTruthy();

    await click(button('Close'), 'Close');
    expect(button('Close')).toBeFalsy();
  });

  it('opens a deal a filter is hiding, rather than calling it deleted', async () => {
    // Membership is asked of the whole payload, never of the filtered view: a session facet
    // says nothing about whether a deal exists, and the sheet opens over the board however
    // few cards the columns are showing.
    // `last_activity_at` is what makes the facet BITE: the 'No activity logged' preset
    // keeps deals with none, so a deal without it (the shared factory's default) is never
    // excluded and this test would pass against a regressed lookup too.
    const HIDDEN = deal({
      id: 9, title: 'Globex expansion', stage: 'won',
      last_activity_at: '2026-08-30T00:00:00+00:00',
    });
    // #124 hides `won` by default, and this test's subject is the FACET. Showing the stage
    // keeps the card's absence attributable to the facet alone — otherwise the precondition
    // below would hold for two reasons and this would silently become a second copy of the
    // hidden-stage test that follows it.
    sessionStorage.setItem('crm_pipeline_hidden_stages', '[]');
    routeApi({ live: [LIVE, HIDDEN] });
    await render('/crm/pipeline');
    await pickActivityFacet('No activity logged');
    // The facet really is hiding it — without this line the rest asserts nothing.
    expect(card('Globex expansion')).toBeFalsy();

    await renderThenNavigate('/crm/pipeline?deal=9');
    expect(deadLinkNotice()).toBeNull();
    expect(button('Close')).toBeTruthy();
  });

  it('un-hides the stage a linked deal sits in, and opens it from the board', async () => {
    // A hidden STAGE is not a facet, and the difference decides whether this works: facets
    // are applied downstream of `items`, while a hidden stage is filtered out of `items`
    // itself (#74) — and `items` is the array `CollectionDetail` resolves `selectedId`
    // against. So a linked deal in a hidden stage has no record to render from the board.
    //
    // This is the ORDINARY path, not an edge: #124 hides `won` and `lost` by default, and
    // the assistant attaches a link to every deal it names, `crm_mark_deal_won` included.
    // `?stage=` has always un-hidden the column it names; `?deal=` now does the same.
    const CLOSED = deal({ id: 9, title: 'Globex expansion', stage: 'won', value: 700 });
    // Answer the detail GET with the real row. `CollectionDetail` falls back to `loadById`
    // for an id `items` does not hold, and leaving that unrouted would make this test fail
    // on the fixture's null rather than on the behaviour — the panel would still be broken,
    // but for a reason production never produces.
    routeApi({
      live: [LIVE, CLOSED],
      over: path => (path === `/api/crm/deals/${CLOSED.id}` ? CLOSED : undefined),
    });
    await render('/crm/pipeline');
    // The precondition, and it is a real one: the default really does hide the column, so
    // the assertions below cannot pass by the stage having been visible all along.
    expect(stageColumn('won')).toBeUndefined();
    expect(card('Globex expansion')).toBeFalsy();

    await renderThenNavigate('/crm/pipeline?deal=9');

    // The link opened the deal rather than accusing it of being deleted...
    expect(deadLinkNotice()).toBeNull();
    expect(button('Close')).toBeTruthy();
    // ...and the column came back with it, so the deal is in `items` and there is a card
    // behind the panel. These two are the assertions that carry the test: without the
    // reveal the panel still OPENS — `loadById` fetches the row `items` is missing — so the
    // two above pass on their own and only these fail.
    expect(stageColumn('won')).toBeTruthy();
    expect(card('Globex expansion')).toBeTruthy();
  });
});
