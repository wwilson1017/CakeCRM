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
//   • restore    — the sheet's row is patched into the board IN PLACE (no refetch), which
//                  is why POST /restore returns the deal instead of {"ok": true}; the patch
//                  MERGES (the detail projection is narrower than the board's), it
//                  invalidates any board GET already in flight (which would otherwise land
//                  afterwards and undo a committed server write), and it drops the id from
//                  the bulk selection, where it can have been sitting since before the
//                  deal was archived.
//   • failure    — a failed non-silent load toasts, because under "Archived only" a
//                  swallowed failure renders an empty board that reads as "none archived".
//
// NOT tested here, and deliberately not faked: that an archived card cannot be DRAGGED.
// `KanbanCard` withholds dnd-kit's `listeners` (React props, not DOM attributes) when the
// card is disabled, so there is nothing to assert in the DOM, and a real pointer-drag
// gesture needs layout rects and pointer capture that jsdom does not provide. The policy
// itself is unit-tested in `shared/dnd/dragDisabled.test.ts`.
import { act } from 'react';
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

const { PipelinePage } = await import('./PipelinePage');
const { ActiveRecordProvider } = await import('./RecordContext');
// PipelinePage reads `?stage=` through useSearchParams, and the deal sheet publishes the
// open record — both are ambient app scaffolding the test supplies rather than the
// component being reshaped to avoid them.
const { MemoryRouter } = await import('react-router-dom');

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

const LIVE_PATH = '/api/crm/deals';
const ARCHIVED_PATH = '/api/crm/deals?include_archived=true';

interface RouteOptions {
  /** Board payload for the plain (live-only) request the server answers unasked. */
  live?: CrmDeal[];
  /** Board payload for `?include_archived=true`. */
  withArchived?: CrmDeal[];
  /** Per-path override; return `undefined` to fall through to the defaults. */
  over?: (path: string) => unknown;
}

/** Route the whole component tree's fetches. The two board paths answer DIFFERENT payloads
 *  on purpose — that is the server contract the facet exists to reach, and routing them
 *  identically would make the fetch-widening assertion meaningless. */
function routeApi(opts: RouteOptions = {}) {
  const live = opts.live ?? [LIVE];
  const withArchived = opts.withArchived ?? [LIVE, ARCHIVED];
  api.mockImplementation(async (path: string) => {
    const custom = opts.over?.(path);
    if (custom !== undefined) return custom;
    if (path === LIVE_PATH) return { deals: live };
    if (path === ARCHIVED_PATH) return { deals: withArchived };
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

async function render() {
  await act(async () => {
    root.render(
      <MemoryRouter><ActiveRecordProvider><PipelinePage /></ActiveRecordProvider></MemoryRouter>,
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

function button(label: string): HTMLButtonElement | undefined {
  return [...container.querySelectorAll('button')]
    .find(b => b.textContent?.trim() === label) as HTMLButtonElement | undefined;
}

/** Open the Archived facet popover and pick one of its two options. */
async function pickArchivedFacet(option: 'Include archived' | 'Archived only') {
  await click(button('Archived'), 'Archived facet');
  await click(button(option), option);
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
  const promise = new Promise<T>(resolve => { settle = resolve; });
  return { promise, resolve: settle };
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

  it('patches a restored deal into the board in place, with no further board fetch', async () => {
    routeApi({ over: path => (path === `/api/crm/deals/${ARCHIVED.id}/restore` ? RESTORED : undefined) });
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
    // ...and none of that depended on a refetch, which is the point: a silent refresh can
    // fail invisibly and leave the board showing a deal as archived after a real restore.
    expect(boardRequests()).toHaveLength(before);
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
      over: path => {
        if (path === `/api/crm/deals/${ARCHIVED.id}/restore`) return RESTORED;
        // The SECOND board GET is the one the sheet's Close fires; hold it open.
        if (path === ARCHIVED_PATH && ++archivedLoads === 2) return held.promise;
        return undefined;
      },
    });
    await render();
    await pickArchivedFacet('Include archived');

    await click(card('Zebra rebuild'), 'archived card');
    await click(button('Close'), 'Close');
    await click(card('Zebra rebuild'), 'archived card again');
    await click(button('Restore'), 'Restore');
    expect(card('Zebra rebuild')?.textContent).not.toContain('ARCHIVED');

    // ...and now the payload that was already in the air lands, still describing the deal
    // as archived. It is stale by construction and must be dropped on the floor.
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
    routeApi({
      live: [LIVE], withArchived: [LIVE, touched],
      over: path => {
        if (path === `/api/crm/deals/2/restore`) return RESTORED;
        if (path === '/api/crm/deals/2') return touched;
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
    // The mirror of the archived-while-selected test above. That one proves the id is
    // MASKED while the deal stays archived; this one proves it is actually GONE, because
    // masking alone is a trap: the selection Set still holds the id, and restoring — a
    // recovery gesture, not a selection one — would hand it straight back to the next bulk
    // move, on a deal the operator selected before it was ever archived.
    const zebraLive = deal({ id: 2, title: 'Zebra rebuild', stage: 'lead', value: 99_999 });
    routeApi({
      live: [LIVE, zebraLive],
      over: path => (path === `/api/crm/deals/2/restore` ? RESTORED : undefined),
    });
    await render();
    await click(cardCheckbox('Zebra rebuild'), 'Zebra rebuild checkbox');
    expect(container.textContent).toContain('1 deal selected');

    // Archived elsewhere (the assistant, a merge) — the id is masked, not dropped.
    await pickArchivedFacet('Include archived');
    expect(container.textContent).not.toContain('deal selected');

    await click(card('Zebra rebuild'), 'archived card');
    await click(button('Restore'), 'Restore');

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
});
