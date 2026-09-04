// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, StrictMode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import type { CrmTimelineEntry, CrmTimelinePage } from '../../core/types';

const apiMock = vi.fn();
vi.mock('../../core/api/client', () => ({
  api: (...args: unknown[]) => apiMock(...args),
  ApiError: class extends Error {},
}));
vi.mock('../useUsers', () => ({ useUsers: () => ({ nameFor: (id: number) => `User ${id}` }) }));

let container: HTMLDivElement;
let root: Root;

function entry(over: Partial<CrmTimelineEntry> = {}): CrmTimelineEntry {
  return {
    source: 'note', id: 1, entity_type: 'deal', entity_id: 5, activity: null,
    message: 'hello', created_at: '2026-03-02T15:00:00+00:00', updated_at: null,
    archived: 0, actor_id: null, source_name: 'Q3 renewal', source_archived: false,
    ...over,
  };
}
const page = (entries: CrmTimelineEntry[], has_more = false): CrmTimelinePage => ({ entries, has_more });

/**
 * Answer by URL rather than by call order. StrictMode double-invokes effects, so a
 * `mockResolvedValueOnce` queue is drained twice and the second mount gets `undefined` —
 * which looks like a component bug and is not one.
 */
function respondByUrl(routes: Array<[RegExp, () => unknown]>): void {
  apiMock.mockImplementation((url: string) => {
    for (const [pattern, answer] of routes) {
      if (!pattern.test(url)) continue;
      // Always a promise: `api` returns one, and a route that throws must REJECT rather
      // than blow up at the call site, or the component never sees the failure it handles.
      try { return Promise.resolve(answer()); } catch (e) { return Promise.reject(e); }
    }
    return Promise.reject(new Error(`unstubbed request: ${url}`));
  });
}

async function settle(rounds = 8): Promise<void> {
  for (let i = 0; i < rounds; i++) await act(async () => { await Promise.resolve(); });
}

async function mount(includeArchived = false): Promise<void> {
  const { CompanyTimeline } = await import('./CompanyTimeline');
  act(() => {
    root.render(
      <StrictMode>
        <MemoryRouter>
          <CompanyTimeline companyId={7} includeArchived={includeArchived} />
        </MemoryRouter>
      </StrictMode>,
    );
  });
  await settle();
}

const text = () => container.textContent ?? '';
const buttonSaying = (label: string) =>
  Array.from(container.querySelectorAll('button')).find(b => b.textContent?.includes(label));
const urls = () => apiMock.mock.calls.map(c => String(c[0]));

beforeEach(() => {
  apiMock.mockReset();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe('CompanyTimeline', () => {
  it('renders the first page grouped by day, each row carrying its source', async () => {
    apiMock.mockResolvedValue(page([
      entry({ id: 1, source_name: 'Q3 renewal' }),
      entry({ id: 2, source: 'activity', activity: 'call', message: '',
              entity_type: 'contact', entity_id: 3, source_name: 'Ada' }),
    ]));
    await mount();
    expect(text()).toContain('Deal · Q3 renewal');
    expect(text()).toContain('Contact · Ada');
    expect(text()).toContain('March 2, 2026');
  });

  it('distinguishes a failed load from an empty account', async () => {
    // Two states that look identical if you only track `entries.length === 0`, and telling a
    // rep an account has no history when the request merely failed is the worse of the two.
    apiMock.mockRejectedValue(new Error('boom'));
    await mount();
    expect(text()).toContain("Couldn't load this timeline");
    expect(text()).not.toContain('No notes or activity');

    apiMock.mockReset();
    apiMock.mockResolvedValue(page([]));
    act(() => { buttonSaying('Try again')!.click(); });
    await settle();
    expect(text()).toContain('No notes or activity on this account yet');
  });

  it('shows the loading state before the first response, never the empty state', async () => {
    let resolve!: (p: CrmTimelinePage) => void;
    apiMock.mockReturnValue(new Promise<CrmTimelinePage>(r => { resolve = r; }));
    await mount();
    expect(text()).toContain('Loading timeline…');
    expect(text()).not.toContain('No notes or activity');
    await act(async () => { resolve(page([])); });
    await settle();
    expect(text()).toContain('No notes or activity');
  });

  it('advances Load more by the rows the SERVER returned, not by the list length', async () => {
    // The regression this pins: a page that is entirely rows we already hold appends nothing,
    // so an offset derived from the list length would re-request that same page forever.
    const dupes = Array.from({ length: 3 }, (_, i) => entry({ id: i + 1 }));
    respondByUrl([
      [/offset=0/, () => page([entry({ id: 1 })], true)],
      [/offset=1/, () => page(dupes, true)],
      [/offset=4/, () => page([entry({ id: 9 })], false)],
    ]);
    await mount();
    act(() => { buttonSaying('Load more')!.click(); });
    await settle();
    act(() => { buttonSaying('Load more')!.click(); });
    await settle();
    // 1 + 3 rows RETURNED, even though only 2 of those 3 were new.
    expect(urls().some(u => u.includes('offset=4'))).toBe(true);
  });

  it('dedupes an appended page on (source, id), never on id alone', async () => {
    respondByUrl([
      [/offset=0/, () => page([entry({ id: 2, source: 'note', message: 'note two' })], true)],
      [/offset=1/, () => page([
        entry({ id: 2, source: 'note', message: 'note two' }),
        entry({ id: 2, source: 'activity', activity: 'call', message: 'activity two' }),
      ])],
    ]);
    await mount();
    act(() => { buttonSaying('Load more')!.click(); });
    await settle();
    expect(text()).toContain('activity two');
    expect(text().match(/note two/g)).toHaveLength(1);
  });

  it('keeps the entries it has when Load more fails', async () => {
    respondByUrl([
      [/offset=0/, () => page([entry({ id: 1, message: 'kept' })], true)],
      [/offset=1/, () => { throw new Error('boom'); }],
    ]);
    await mount();
    act(() => { buttonSaying('Load more')!.click(); });
    await settle();
    expect(text()).toContain('kept');
    expect(text()).toContain("Couldn't load more of the timeline");
  });

  it('refetches from offset 0 when the archived filter changes', async () => {
    apiMock.mockResolvedValue(page([entry({ id: 1 })], true));
    await mount(false);
    apiMock.mockResolvedValue(page([entry({ id: 5, message: 'archived note' })]));
    const { CompanyTimeline } = await import('./CompanyTimeline');
    act(() => {
      root.render(
        <StrictMode>
          <MemoryRouter>
            <CompanyTimeline companyId={7} includeArchived />
          </MemoryRouter>
        </StrictMode>,
      );
    });
    await settle();
    const last = urls()[urls().length - 1];
    expect(last).toContain('offset=0');
    expect(last).toContain('include_archived=true');
    expect(text()).toContain('archived note');
  });

  it('discards a first-page response that a filter change has superseded', async () => {
    // Without the request key, the slow unfiltered page lands after the filtered one and
    // shows rows the current filter excludes.
    let resolveSlow!: (p: CrmTimelinePage) => void;
    const slow = new Promise<CrmTimelinePage>(r => { resolveSlow = r; });
    respondByUrl([
      [/include_archived=true/, () => page([entry({ id: 5, message: 'filtered row' })])],
      [/./, () => slow],
    ]);
    await mount(false);

    const { CompanyTimeline } = await import('./CompanyTimeline');
    act(() => {
      root.render(
        <StrictMode>
          <MemoryRouter>
            <CompanyTimeline companyId={7} includeArchived />
          </MemoryRouter>
        </StrictMode>,
      );
    });
    await settle();

    await act(async () => { resolveSlow(page([entry({ id: 1, message: 'stale row' })])); });
    await settle();
    expect(text()).toContain('filtered row');
    expect(text()).not.toContain('stale row');
  });

  it('marks an archived source and still links a contact source', async () => {
    apiMock.mockResolvedValue(page([
      entry({ id: 1, entity_type: 'contact', entity_id: 10, source_name: 'Ada', source_archived: true }),
    ]));
    await mount(true);
    const link = container.querySelector('a[href="/crm/contacts/10"]');
    expect(link?.textContent).toContain('(archived)');
  });
});
