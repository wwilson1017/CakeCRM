// @vitest-environment jsdom
//
// The client half of issue #83's load-bearing invariant: an archived deal must be
// FINDABLE, must never be MONEY, and must never be ACTIONABLE.
//
// The server half already has tests (`get_pipeline` keeps `LIVE_PREDICATE` on
// `stage_summary` even when `include_archived=true` widens the deals query). The split is
// deliberate and asymmetric — cards and totals describe different sets on purpose — so the
// board is the only place the two halves are reconciled, and a regression here would be
// silent: an archived deal quietly re-entering the open-pipeline number, or picking up a
// bulk checkbox that lets an operator send a stage move the server will refuse.
//
// What is pinned below, in the order the invariant states it:
//   • money      — column $ and the open-pipeline header count LIVE deals only, while the
//                  archived card is right there in the same column.
//   • findable   — the archived card renders, labelled ARCHIVED.
//   • inert      — no per-card checkbox, and an all-archived column offers no select-all.
//   • the fetch  — the Archived facet is the ONE facet that widens the request, because a
//                  client predicate cannot filter rows the server never sent.
//   • restore    — the sheet's row is patched into the board IN PLACE, which is why POST
//                  /restore returns the deal instead of {"ok": true}: the patch is what
//                  makes the board correct, and it does not wait on the refresh that
//                  follows it (that GET is silent and can fail invisibly). The patch
//                  MERGES (the detail projection is narrower than the board's), no board
//                  GET already in flight may land after it and undo a committed server
//                  write, and it drops the id from the bulk selection, where it can have
//                  been sitting since before the deal was archived.
//   • failure    — a failed non-silent load toasts, because under "Archived only" a
//                  swallowed failure renders an empty board that reads as "none archived".
//   • deferral   — the facet made a load a USER ACTION, so the "don't clobber an optimistic
//                  drag" rule stopped being a silent-load rule. A deferred load re-fires
//                  WIDENED rather than reverting to the facet it was created under; it
//                  still REPORTS its failure rather than laundering it into a silent
//                  refresh, while deliberately not taking the page back to do so; and it
//                  re-fires ITSELF when the write that invalidated it had already settled,
//                  leaving nobody else to.
//   • selection  — the prune intersects with the payload's LIVE ids, so a deal that is
//                  archived OR gone loses its selection and a later restore cannot re-arm it.
//
// NOT tested here, and deliberately not faked: that an archived card cannot be DRAGGED.
// `KanbanCard` withholds dnd-kit's `listeners` (React props, not DOM attributes) when the
// card is disabled, so there is nothing to assert in the DOM, and a real pointer-drag
// gesture needs layout rects and pointer capture that jsdom does not provide. The policy
// itself is unit-tested in `shared/dnd/dragDisabled.test.ts`. Note this is about the
// PROHIBITION: the `shared/dnd` mock below can complete a PERMITTED move by calling the
// board's `onMove`, which proves nothing about what dnd-kit would have refused to start.
//
// Also NOT tested, for a sharper reason: `load`'s spinner generation. Its whole subject is
// two overlapping NON-SILENT loads, and no such pair is reachable — `if (loading)` returns
// the spinner INSTEAD OF the board, so from the moment a non-silent load starts there is no
// filter bar, no card, no sheet and no form left in the DOM to start a second load of any
// kind from. (Verified, not assumed: with a non-silent load held in flight,
// `container.querySelectorAll('button')` is empty and the DOM is the spinner div alone.) So
// a test would have to reach past the component's surface to fire the second load itself,
// which would pin the harness rather than the component. The generation is kept because it
// is the rule that stays correct if that surface ever changes — a board rendered ALONGSIDE
// the spinner, or any second non-silent trigger, makes it load-bearing immediately.
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
const { MemoryRouter, useNavigate } = await import('react-router-dom');

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
const RESTORED = deal({ id: 2, title: 'Zebra rebuild', stage: 'lead', value: 99_999, archived_at: null });

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
  // The filter envelope is persisted to sessionStorage, so a facet left on by one test
  // would silently arm the next one.
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

/** Navigate a MOUNTED page to a new URL — the deep-link case that matters most, since the
 *  assistant's drawer is a slide-over: clicking a link it produced changes the search
 *  params of a page that is already showing a board, without remounting anything. A plain
 *  `render()` cannot express that; it would always look like a cold load. */
function Navigator({ to }: { to: string | null }) {
  const navigate = useNavigate();
  useEffect(() => { if (to) navigate(to); }, [navigate, to]);
  return null;
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

async function flush() {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

async function click(el: Element | null | undefined, what: string) {
  if (!el) throw new Error(`nothing to click: ${what}`);
  await act(async () => { (el as HTMLElement).click(); });
  await flush();
}

function button(label: string): HTMLButtonElement | undefined {
  return [...container.querySelectorAll('button')]
    .find(b => b.textContent?.trim() === label) as HTMLButtonElement | undefined;
}

/** Open the Archived facet popover and pick one of its two options. */
async function pickArchivedFacet(option: 'Include archived' | 'Archived only') {
  await click(button('Archived'), 'Archived facet');
  await click(button(option), option);
}

/** Take an active facet back off through its pill — the way back to the live-only board,
 *  and the only one that stays clickable once the facet button carries the chosen label. */
async function removePill(label: string) {
  await click(
    container.querySelector(`button[aria-label="Remove ${label} filter"]`),
    `remove ${label} pill`,
  );
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

/** Open the Deal-activity facet popover and pick one of its buckets. The facet button is
 *  labelled 'Deal activity' until something is chosen, which is all these tests need. */
async function pickActivityFacet(option: 'No activity logged') {
  await click(button('Deal activity'), 'Deal activity facet');
  await click(button(option), option);
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

function stageColumn(stage: string): HTMLElement {
  const el = container.querySelector(`[data-stage="${stage}"]`);
  if (!el) throw new Error(`no rendered column for stage "${stage}"`);
  return el as HTMLElement;
}

/** The `$N` a stage column's header reports. Structural (the header is the column's first
 *  child, the total its last span) — a layout change makes this throw or mismatch rather
 *  than quietly matching a substring of a wrong number. */
function columnTotal(stage: string): string {
  const header = stageColumn(stage).firstElementChild;
  return header?.lastElementChild?.textContent ?? '';
}

function card(title: string): HTMLElement | undefined {
  return [...container.querySelectorAll('[role="button"]')]
    .find(el => el.textContent?.includes(title)) as HTMLElement | undefined;
}

function cardCheckbox(title: string): HTMLInputElement | null {
  return container.querySelector(`input[aria-label="Select ${title}"]`);
}

function selectAllCheckbox(stage: string): HTMLInputElement | null {
  return container.querySelector(`input[aria-label="Select all ${stage} deals"]`);
}

/** Every BOARD request made so far, in order. Child components fetch too, so filtering to
 *  the two board paths is what makes "the facet drives the fetch" a statement about the
 *  board rather than about traffic in general. */
function boardRequests(): string[] {
  return api.mock.calls
    .map(c => String(c[0]))
    .filter(p => p === LIVE_PATH || p === ARCHIVED_PATH);
}

describe('PipelinePage — archived deals', () => {
  it('counts only LIVE deals as money while showing the archived card beside them', async () => {
    routeApi();
    await render();
    await pickArchivedFacet('Include archived');

    // Both cards are on the board...
    expect(card('Acme renewal')).toBeTruthy();
    expect(card('Zebra rebuild')).toBeTruthy();
    // ...and exactly one of them is pipeline. 100, never 100,099.
    expect(columnTotal('lead')).toBe('$100');
    // formatNumber renders 100099 as "100K", so a regression here is unmistakable.
    expect(container.textContent).toContain('$100 open · 1 open deal');
  });

  it('renders the archived card as findable but inert — labelled, with no checkbox', async () => {
    routeApi();
    await render();
    await pickArchivedFacet('Include archived');

    expect(card('Zebra rebuild')?.textContent).toContain('ARCHIVED');
    // Not selectable: the server refuses a stage change on an archived deal, so a bulk
    // checkbox could only ever offer a move that comes back as a per-deal error.
    expect(cardCheckbox('Zebra rebuild')).toBeNull();
    // The live card in the same column proves the absence is about `archived`, not about
    // checkboxes being switched off wholesale (mobile, a bulk move in flight).
    expect(cardCheckbox('Acme renewal')).not.toBeNull();
  });

  it('select-all in a mixed column selects the live deal only', async () => {
    routeApi();
    await render();
    await pickArchivedFacet('Include archived');
    await click(selectAllCheckbox('lead'), 'select-all for lead');

    expect(container.textContent).toContain('1 deal selected');
    expect(container.textContent).not.toContain('2 deals selected');
  });

  it('offers no select-all at all in an all-archived column', async () => {
    // The column-level half of the same rule, and the half that is falsifiable on its own:
    // with archived ids filtered out, this column contributes none, so the header checkbox
    // has nothing to offer and does not render.
    routeApi({ live: [], withArchived: [ARCHIVED] });
    await render();
    await pickArchivedFacet('Include archived');

    expect(card('Zebra rebuild')).toBeTruthy();
    expect(selectAllCheckbox('lead')).toBeNull();
  });

  it('drops a deal from the bulk payload once it comes back archived', async () => {
    // The reachable version of "selected, then archived elsewhere" (the assistant, a
    // merge): the deal is selected while live, and the next board payload says it is
    // archived. The selection Set still holds its id, so the intersection with the LIVE
    // subset is the only thing standing between the operator and a bulk move the server
    // would refuse deal-by-deal.
    const archivedLater = { ...LIVE, archived_at: '2026-08-24T00:00:00+00:00' };
    routeApi({ live: [LIVE], withArchived: [archivedLater] });
    await render();
    await click(cardCheckbox('Acme renewal'), 'Acme renewal checkbox');
    expect(container.textContent).toContain('1 deal selected');

    await pickArchivedFacet('Include archived');
    expect(card('Acme renewal')?.textContent).toContain('ARCHIVED');
    expect(container.textContent).not.toContain('deal selected');
  });

  it('widens the FETCH when the facet turns on, and not before', async () => {
    routeApi();
    await render();
    // Archived rows are swept out server-side, so the default request must stay narrow.
    expect(boardRequests()).toEqual([LIVE_PATH]);

    await pickArchivedFacet('Include archived');
    expect(boardRequests()).toEqual([LIVE_PATH, ARCHIVED_PATH]);
  });

  it('applies a restore IMMEDIATELY, and only then refreshes behind it', async () => {
    // The board must be right the instant `POST /restore` returns, not once a follow-up GET
    // lands: that GET is silent and can fail invisibly, which would leave the board calling
    // a deal archived after a restore the server actually performed. So the patch is the
    // correctness mechanism and the refresh is additive — it exists because a restore closes
    // the sheet exactly as Close does, and that path refreshes so an in-sheet note reaches
    // the board's derived `last_activity_at`.
    //
    // The refresh is therefore held open across the assertions. That is not an artificial
    // pause: it is precisely the case the patch was designed for — a refresh that is slow,
    // fails, or never arrives — with the board still expected to be correct.
    const refresh = deferred<{ deals: CrmDeal[] }>();
    let archivedLoads = 0;
    routeApi({
      over: path => (path === ARCHIVED_PATH && ++archivedLoads === 2 ? refresh.promise : undefined),
    });
    await render();
    await pickArchivedFacet('Include archived');
    const before = boardRequests().length;

    await click(card('Zebra rebuild'), 'archived card');
    await click(button('Restore'), 'Restore');

    // The row the server returned is now the board's row: it is money again...
    expect(columnTotal('lead')).toBe('$100,099');
    // ...it has lost the ARCHIVED label and gained a bulk checkbox...
    expect(card('Zebra rebuild')?.textContent).not.toContain('ARCHIVED');
    expect(cardCheckbox('Zebra rebuild')).not.toBeNull();
    // ...all of it with the refresh still in the air. The refresh WAS issued — the sheet's
    // own Close fires one too — it simply has no say in whether the board above is right.
    expect(boardRequests()).toHaveLength(before + 1);

    // And when it does land it agrees, rather than undoing anything.
    await act(async () => { refresh.resolve({ deals: [LIVE, RESTORED] }); });
    await flush();
    expect(columnTotal('lead')).toBe('$100,099');
    expect(card('Zebra rebuild')?.textContent).not.toContain('ARCHIVED');
  });

  it('discards a board load that was already in flight when the restore landed', async () => {
    // The restore is the only write on this page the server commits WITHOUT the board
    // starting it, so it is the only one a stale GET can silently undo. The reachable
    // ordering: closing the sheet fires a silent refresh, the user reopens the archived
    // deal and restores it, and only then does that GET come back — carrying a payload
    // requested BEFORE the restore, which still calls the deal archived. Applying it would
    // put the deal back in a state the server no longer holds, with nothing on screen to
    // say so. `writeGen` doesn't cover this: it guards a refresh against a racing *write*,
    // and a restore never touches the drag bookkeeping it counts.
    const held = deferred<{ deals: CrmDeal[] }>();
    let archivedLoads = 0;
    routeApi({
      // The SECOND board GET is the one the sheet's Close fires; hold it open. The THIRD is
      // the restore's own follow-up refresh, which is left to resolve normally against a
      // server that now reports the deal live.
      over: path => (path === ARCHIVED_PATH && ++archivedLoads === 2 ? held.promise : undefined),
    });
    await render();
    await pickArchivedFacet('Include archived');

    await click(card('Zebra rebuild'), 'archived card');
    await click(button('Close'), 'Close');
    await click(card('Zebra rebuild'), 'archived card again');
    await click(button('Restore'), 'Restore');
    await flush();
    expect(card('Zebra rebuild')?.textContent).not.toContain('ARCHIVED');

    // ...and only NOW does the payload that was already in the air land, still describing
    // the deal as archived. Resolving it LAST is what makes this a test of the discard and
    // not of the refresh: nothing follows it that could quietly put the board right again,
    // so if it were applied the board would end the test showing the deal archived.
    await act(async () => { held.resolve({ deals: [LIVE, ARCHIVED] }); });
    await flush();

    expect(card('Zebra rebuild')?.textContent).not.toContain('ARCHIVED');
    expect(columnTotal('lead')).toBe('$100,099');
  });

  it('MERGES the restored row into the board rather than replacing it', async () => {
    // Two different projections of one deal: `POST /restore` answers with `get_deal`'s,
    // while the board's rows come from `get_pipeline`, which additionally derives
    // `last_activity_at`. Swapping the row wholesale drops that field — and a dropped
    // field is not a blank cell here, it is a wrong answer: the deal falls into the
    // Deal-activity facet's "No activity logged" bucket, so a deal with a fortnight of
    // logged calls on it reads as never touched the moment it is restored.
    const touched = deal({
      id: 2, title: 'Zebra rebuild', stage: 'lead', value: 99_999,
      archived_at: '2026-08-20T00:00:00+00:00',
      last_activity_at: new Date().toISOString(),
    });
    // RESTORED carries no `last_activity_at` KEY at all (not the key set to undefined),
    // which is exactly what the detail projection sends — and what makes a merge preserve
    // the board's copy rather than blank it.
    const refresh = deferred<{ deals: CrmDeal[] }>();
    let archivedLoads = 0;
    routeApi({
      live: [LIVE], withArchived: [LIVE, touched],
      over: path => {
        if (path === '/api/crm/deals/2') return touched;
        // Hold the restore's follow-up refresh open for good. The server would re-supply
        // `last_activity_at` on that GET and paper straight over a patch that dropped it —
        // which is the whole failure this test exists to catch, and would make it vacuous.
        // The board's guarantee is that the PATCH is right on its own.
        if (path === ARCHIVED_PATH && ++archivedLoads === 2) return refresh.promise;
        return undefined;
      },
    });
    await render();
    await pickArchivedFacet('Include archived');
    await click(card('Zebra rebuild'), 'archived card');
    await click(button('Restore'), 'Restore');

    await pickActivityFacet('No activity logged');
    // Acme has genuinely never been touched, so the bucket is non-empty either way and the
    // board keeps rendering — the only question the assertion asks is whether the restored
    // deal joined it.
    expect(card('Acme renewal')).toBeTruthy();
    expect(card('Zebra rebuild')).toBeUndefined();
  });

  it('drops a restored deal from the bulk selection instead of silently re-arming it', async () => {
    // The mirror of the archived-while-selected test above — and deliberately the case that
    // one does NOT cover. There, a board payload reports the deal archived, and `load`'s own
    // intersection drops the id before any restore happens. Here NO payload ever does: the
    // deal is archived elsewhere AFTER the board loaded, so the board's row still says live
    // and the only thing that knows better is the sheet's re-fetched `archived_at` — which
    // is exactly why the sheet re-fetches it. So the id is still in the Set at the moment
    // the user restores, and restoring is a recovery gesture, not a selection one: it must
    // not hand the deal to the next bulk move. The follow-up refresh cannot clean up after
    // it either — that payload reports the deal LIVE, so an id that survives the restore
    // survives the refresh too.
    const zebraLive = deal({ id: 2, title: 'Zebra rebuild', stage: 'lead', value: 99_999 });
    const zebraArchived = { ...zebraLive, archived_at: '2026-08-24T00:00:00+00:00' };
    routeApi({
      live: [LIVE, zebraLive],
      over: path => (path === '/api/crm/deals/2' ? zebraArchived : undefined),
    });
    await render();
    await click(cardCheckbox('Zebra rebuild'), 'Zebra rebuild checkbox');
    expect(container.textContent).toContain('1 deal selected');

    // The board still shows it as an ordinary live card; the sheet is where it turns out to
    // be archived, and where the way back is offered.
    await click(card('Zebra rebuild'), 'Zebra rebuild card');
    await click(button('Restore'), 'Restore');
    await flush();

    expect(cardCheckbox('Zebra rebuild')?.checked).toBe(false);
    expect(container.textContent).not.toContain('deal selected');
  });

  it('says so when a non-silent load fails instead of rendering a misleading board', async () => {
    // Once `data` exists a failed load is invisible — the previous payload keeps
    // rendering, and under an archived facet that reads as "you have no archived deals".
    routeApi({
      over: path => {
        if (path === ARCHIVED_PATH) throw new Error('network down');
        return undefined;
      },
    });
    await render();
    expect(toast.error).not.toHaveBeenCalled();

    await pickArchivedFacet('Archived only');
    expect(toast.error).toHaveBeenCalledWith('Failed to load deals.');
  });

  it('defers a facet load while a stage write is in flight, then re-fires it WIDENED', async () => {
    // Deferring around an in-flight write used to be a SILENT-load rule, which was safe
    // while every load was a refresh of the same content set. The Archived facet made a load
    // a user-initiated action, and this is the sequence that breaks: Mark Won moves the card
    // optimistically, its PUT is still in the air, and the user reaches for the facet. That
    // GET was requested against the PRE-write server state, so applying it slides the card
    // back out of the column the user just watched it land in — while the write is still on
    // its way to succeeding, so nothing on screen ever explains the jump.
    //
    // Deferring is not dropping, and the second half of the test is the half that says so:
    // the load re-fires once writes settle, and reads the CURRENT facet from the ref, so the
    // user's widening survives the wait.
    const put = deferred<CrmDeal>();
    routeApi({
      // The widened payload stays the PRE-write board (the deal still in `lead`) for every
      // call — so the card sitting in `won` below can only be the optimistic write, never a
      // payload that happened to agree with it.
      withArchived: [LIVE, ARCHIVED],
      over: (path, init) => (
        path === `/api/crm/deals/${LIVE.id}` && init?.method === 'PUT' ? put.promise : undefined
      ),
    });
    await render();

    await click(card('Acme renewal'), 'live card');
    await click(button('Mark Won'), 'Mark Won');
    expect(stageColumn('won').textContent).toContain('Acme renewal');

    await pickArchivedFacet('Include archived');
    // The harm first: the card has not slid back to the column the server last knew about.
    expect(stageColumn('won').textContent).toContain('Acme renewal');
    expect(stageColumn('lead').textContent).not.toContain('Acme renewal');
    // ...then the mechanism: not fetched at all. Deferred BEFORE the GET, not filtered
    // after it — a payload that never arrives cannot be applied by a later refactor either.
    expect(boardRequests()).toEqual([LIVE_PATH]);

    // The PUT lands. The deferred load now runs — and asks for the archived rows the user
    // requested while it was waiting, not the live-only board it was created under.
    await act(async () => { put.resolve({ ...LIVE, stage: 'won' }); });
    await flush();
    await flush();
    expect(boardRequests()).toEqual([LIVE_PATH, ARCHIVED_PATH]);
  });

  it('drops a selected id the moment a payload reports it archived, not just from the view', async () => {
    // Masking is not dropping, and the difference only becomes visible later. While the deal
    // stays archived the id is filtered out of the bar and the payload, so the two behave
    // identically; the moment the deal comes BACK — restored by the assistant, another tab,
    // a merge undone — a surviving id rejoins the next bulk move on a deal the operator
    // selected before any of that happened. The board is the only place that can notice:
    // nothing else sees both the selection and the fresh row.
    const zebraLive = deal({ id: 2, title: 'Zebra rebuild', stage: 'lead', value: 99_999 });
    const zebraArchived = { ...zebraLive, archived_at: '2026-08-24T00:00:00+00:00' };
    routeApi({ live: [LIVE, zebraLive], withArchived: [LIVE, zebraArchived] });
    await render();
    await click(cardCheckbox('Zebra rebuild'), 'Zebra rebuild checkbox');
    expect(container.textContent).toContain('1 deal selected');

    // Archived elsewhere. This payload — not the click, not the sheet — is what prunes.
    await pickArchivedFacet('Include archived');
    expect(card('Zebra rebuild')?.textContent).toContain('ARCHIVED');

    // ...and restored elsewhere. Narrowing back to the live board brings the row back, and
    // it must not bring the selection back with it.
    await removePill('Include archived');
    expect(card('Zebra rebuild')?.textContent).not.toContain('ARCHIVED');
    expect(cardCheckbox('Zebra rebuild')?.checked).toBe(false);
    expect(container.textContent).not.toContain('deal selected');
  });

  it('drops a selection when the deal goes ABSENT from a payload, and does not resurrect it', async () => {
    // The other half of the same rule. An earlier revision kept the selection here, on the
    // theory that "absent" might only mean "filtered out" — it cannot. This payload is
    // `get_pipeline`: unpaginated, and carrying no server-side filter the board ever sets.
    // So on a live-only fetch, absent means archived or deleted, and the archive-then-
    // restore-elsewhere sequence below is precisely the resurrection to prevent: the deal
    // comes back on screen, and it must come back UNSELECTED, because the operator never
    // selected the thing that returned.
    const zebraLive = deal({ id: 2, title: 'Zebra rebuild', stage: 'lead', value: 99_999 });
    let liveCalls = 0;
    routeApi({
      over: path => {
        if (path !== LIVE_PATH) return undefined;
        // 1: both deals. 2: zebra archived elsewhere, so the sweep drops it from the narrow
        // board. 3: restored elsewhere, so it is back — still live, still not re-selected
        // by anything the user did.
        liveCalls++;
        return { deals: liveCalls === 2 ? [LIVE] : [LIVE, zebraLive] };
      },
    });
    await render();
    await click(cardCheckbox('Zebra rebuild'), 'Zebra rebuild checkbox');
    expect(container.textContent).toContain('1 deal selected');

    // Two silent refreshes, fired the way a closing detail sheet fires them.
    await reopenAndClose('Acme renewal');
    expect(card('Zebra rebuild')).toBeUndefined();
    expect(container.textContent).not.toContain('deal selected');

    await reopenAndClose('Acme renewal');
    expect(cardCheckbox('Zebra rebuild')?.checked).toBe(false);
    expect(container.textContent).not.toContain('deal selected');
  });

  it('reports a deferred facet load\'s failure without taking the page to do it', async () => {
    // Deferral must not launder a user-initiated load into a background one — but the two
    // halves of "user-initiated" part company here, and only one of them survives the
    // replay. Error REPORTING is carried across (`reportErrors`): a swallowed failure
    // leaves the previous payload on screen, which under "Archived only" is an empty board
    // — the exact false answer "you have no archived deals" to a question the server never
    // answered. The SPINNER is deliberately not carried across, because `loading` returns
    // the spinner INSTEAD of the page, and a replay fires whenever a write happens to
    // settle: taking the page at that moment blanks an open form mid-edit and loses what
    // the user typed. So: the toast fires, and the board stays put.
    const put = deferred<CrmDeal>();
    // The replayed GET is HELD, not failed outright. That is the whole point: a replay that
    // takes the page does so only WHILE its request is in flight, so a mock that rejects
    // immediately puts the page back before any assertion can see it — and the spinner half
    // of this test passes against the bug it exists to catch. Verified: with an
    // instantly-failing mock, reverting a replay site to loud left all 16 tests green.
    const replayed = deferred<{ deals: CrmDeal[] }>();
    routeApi({
      over: (path, init) => {
        if (path === `/api/crm/deals/${LIVE.id}` && init?.method === 'PUT') return put.promise;
        // The ONLY widened GET this test ever issues is the replayed one, because the facet
        // flip below is deferred before it can fetch.
        if (path === ARCHIVED_PATH) return replayed.promise;
        return undefined;
      },
    });
    await render();

    await click(card('Acme renewal'), 'live card');
    await click(button('Mark Won'), 'Mark Won');
    await pickArchivedFacet('Archived only');
    // Nothing has been fetched yet, so nothing has failed yet — this pins that the toast
    // below comes from the REPLAY and not from the original load.
    expect(toast.error).not.toHaveBeenCalled();

    // The write settles, so the deferred load replays — and its GET is now in flight.
    await act(async () => { put.resolve({ ...LIVE, stage: 'won' }); });
    await flush();

    // THE HALF THAT MATTERS, asserted while the replay is still running. When `loading` is
    // true this container holds the spinner and NOTHING else — no heading, no filter bar,
    // no open form. So the heading still being here says the replay did not take the
    // screen out from under whatever the user was doing when the write happened to settle.
    expect(container.textContent).toContain('Pipeline');
    expect(container.querySelector('.animate-spin')).toBeNull();

    // ...and now it fails, and the failure is still REPORTED despite having been demoted
    // to a background request — the other half of the split.
    await act(async () => { replayed.reject(new Error('network down')); });
    await flush();
    expect(toast.error).toHaveBeenCalledWith('Failed to load deals.');
  });

  it('re-fires a load that the settling write left nobody to replay', async () => {
    // Deferral has two halves: refuse the stale payload, and make sure someone asks again.
    // Normally the settling write's `finally` is that someone — but the write can START AND
    // FINISH entirely inside the GET's flight, in which case its finally already ran and
    // saw nothing pending. The load is then dropped outright and NOBODY asks again, so the
    // board keeps rendering a payload from before the write until the user happens to do
    // something else. A drag during the sheet's closing refresh is exactly that timing, and
    // it is reachable only through a drag — every other write on this page calls `load`
    // itself and so leaves a deferral behind for its finally to replay.
    const held = deferred<{ deals: CrmDeal[] }>();
    // On the board only after the re-fire: nothing else can put this card on screen, so it
    // is proof a THIRD request was made and applied, not merely issued.
    const GLOBEX = deal({ id: 3, title: 'Globex expansion', stage: 'qualified', value: 500 });
    const WON = { ...LIVE, stage: 'won' };
    let liveCalls = 0;
    routeApi({
      over: (path, init) => {
        if (path === `/api/crm/deals/${LIVE.id}` && init?.method === 'PUT') return WON;
        if (path !== LIVE_PATH) return undefined;
        liveCalls++;
        // 1: mount. 2: the sheet's closing refresh, held open across the drag below.
        // 3: the request the dropped load has to make for itself.
        if (liveCalls === 2) return held.promise;
        return { deals: liveCalls === 1 ? [LIVE] : [WON, GLOBEX] };
      },
    });
    await render();

    // A silent refresh is now in the air. It leaves the board interactive, which is what
    // makes the next line reachable at all — a non-silent load would have replaced the
    // whole board with the spinner.
    await reopenAndClose('Acme renewal');
    expect(boardRequests()).toHaveLength(2);

    // The drag starts and finishes inside that GET's flight. It never called `load`, so its
    // `finally` finds nothing deferred and replays nothing.
    await fireDrag(LIVE.id, 'won');
    await flush();
    expect(stageColumn('won').textContent).toContain('Acme renewal');

    // Now the held payload lands, describing the board as it was before the drag. Dropping
    // it is right; stopping there is not.
    await act(async () => { held.resolve({ deals: [LIVE] }); });
    await flush();
    await flush();

    // The board caught up with the server on its own...
    expect(card('Globex expansion')).toBeTruthy();
    // ...by making exactly one more request, not by being lucky.
    expect(boardRequests()).toHaveLength(3);
  });
});


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
    routeApi({ live: [LIVE, deal({ id: 9, title: 'Globex expansion', stage: 'won' })] });
    await render('/crm/pipeline');
    await pickActivityFacet('No activity logged');

    await renderThenNavigate('/crm/pipeline?deal=9');
    expect(deadLinkNotice()).toBeNull();
    expect(button('Close')).toBeTruthy();
  });
});
