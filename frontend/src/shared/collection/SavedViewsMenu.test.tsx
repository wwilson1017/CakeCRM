// @vitest-environment jsdom
//
// The saved-views control (#181). The load-bearing behaviors: nothing is fetched until the
// menu opens (four surfaces would otherwise each cost a request per page load), a stale-version
// view is shown-but-disabled rather than hidden or applied, `can_edit` from the server is the
// only thing that gates the edit actions, and a superseded response can never overwrite a
// fresher one.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

const apiMock = vi.fn();
class FakeApiError extends Error {
  status = 0;
  detail = '';
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}
vi.mock('../../core/api/client', () => ({
  api: (...args: unknown[]) => apiMock(...args),
  ApiError: FakeApiError,
}));

const confirmMock = vi.fn();
vi.mock('../confirm', () => ({ confirmDialog: (...a: unknown[]) => confirmMock(...a) }));

const toastMock = { success: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../toast', () => ({ toast: toastMock }));

const { default: SavedViewsMenu } = await import('./SavedViewsMenu');
import type { CollectionSnapshot, CollectionStorage } from './types';
import type { SnapshotSource } from './savedViews';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const STORAGE: CollectionStorage = { key: 'crm_pipeline', version: 2 };

const SOURCE: SnapshotSource = {
  query: 'acme',
  facetSelections: { stage: ['new'] },
  voided: null,
  sort: { field: 'name', dir: 'asc' },
  view: 'kanban',
};

const EXPECTED_SNAPSHOT: CollectionSnapshot = {
  query: 'acme',
  facets: { stage: ['new'] },
  voided: null,
  sort: { field: 'name', dir: 'asc' },
  view: 'kanban',
};

function view(over: Record<string, unknown> = {}) {
  return {
    id: 1,
    surface: 'crm_pipeline',
    name: 'Q3 pipeline',
    version: 2,
    payload: { query: 'saved' },
    created_by: 7,
    created_by_name: 'Casey Creator',
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z',
    can_edit: true,
    ...over,
  };
}

let container: HTMLDivElement;
let root: Root;
let applySnapshot: Mock<(raw: unknown) => void>;
let onApplied: Mock<() => void>;

function render() {
  act(() =>
    root.render(
      <SavedViewsMenu
        storage={STORAGE}
        state={{ ...SOURCE, applySnapshot }}
        onApplied={onApplied}
      />,
    ),
  );
}

const buttons = () => [...document.querySelectorAll('button')];
const byText = (text: string) => {
  const el = buttons().find(b => b.textContent?.trim() === text);
  if (!el) throw new Error(`no button "${text}" — have: ${buttons().map(b => b.textContent).join(' | ')}`);
  return el;
};
const click = async (el: Element) => {
  await act(async () => {
    (el as HTMLElement).click();
  });
};

async function open() {
  await click(byText('Views'));
}

function setInputValue(input: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
  act(() => {
    setter?.set?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

async function submitName(name: string) {
  const input = document.querySelector<HTMLInputElement>('input[aria-label="View name"]');
  if (!input) throw new Error('no name field');
  setInputValue(input, name);
  await act(async () => {
    input.form?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
}

beforeEach(() => {
  apiMock.mockReset();
  confirmMock.mockReset();
  toastMock.success.mockReset();
  toastMock.error.mockReset();
  applySnapshot = vi.fn<(raw: unknown) => void>();
  onApplied = vi.fn<() => void>();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('fetching', () => {
  it('requests nothing on mount', () => {
    apiMock.mockResolvedValue({ views: [] });
    render();
    expect(apiMock).not.toHaveBeenCalled();
  });

  it('fetches the surface’s views when opened', async () => {
    apiMock.mockResolvedValue({ views: [view()] });
    render();
    await open();
    expect(apiMock).toHaveBeenCalledOnce();
    expect(apiMock.mock.calls[0][0]).toBe('/api/saved-views?surface=crm_pipeline');
    expect(document.body.textContent).toContain('Q3 pipeline');
    expect(document.body.textContent).toContain('by Casey Creator');
  });

  it('shows a retry affordance when the load fails', async () => {
    apiMock.mockRejectedValue(new FakeApiError(500, 'Server is down'));
    render();
    await open();
    expect(document.body.textContent).toContain('Server is down');
    apiMock.mockResolvedValue({ views: [view()] });
    await click(byText('Retry'));
    expect(document.body.textContent).toContain('Q3 pipeline');
  });

  it('survives a response body that is not the expected envelope', async () => {
    apiMock.mockResolvedValue({ unexpected: true });
    render();
    await open();
    expect(document.body.textContent).toContain('No saved views yet');
  });

  it('says so when a surface has no views yet', async () => {
    apiMock.mockResolvedValue({ views: [] });
    render();
    await open();
    expect(document.body.textContent).toContain('No saved views yet');
  });

  it('ignores a superseded response', async () => {
    // Open (slow request), close, reopen (fast request). The first response settles LAST and
    // must be dropped, or a stale list replaces the fresh one.
    let settleFirst: (v: unknown) => void = () => {};
    apiMock.mockImplementationOnce(() => new Promise(res => { settleFirst = res; }));
    render();
    await open();
    await click(byText('Views'));
    apiMock.mockResolvedValueOnce({ views: [view({ name: 'Fresh' })] });
    await open();
    expect(document.body.textContent).toContain('Fresh');
    await act(async () => {
      settleFirst({ views: [view({ name: 'Stale' })] });
    });
    expect(document.body.textContent).toContain('Fresh');
    expect(document.body.textContent).not.toContain('Stale');
  });
});

describe('applying', () => {
  it('hands the raw payload to applySnapshot, notifies the shell and closes', async () => {
    apiMock.mockResolvedValue({ views: [view()] });
    render();
    await open();
    await click(byText('Q3 pipeline'));
    expect(applySnapshot).toHaveBeenCalledWith({ query: 'saved' });
    expect(onApplied).toHaveBeenCalledOnce();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });

  it('shows a view saved under another version disabled, with the reason, and never applies it', async () => {
    apiMock.mockResolvedValue({ views: [view({ version: 1 })] });
    render();
    await open();
    const row = byText('Q3 pipeline');
    expect(row.getAttribute('aria-disabled')).toBe('true');
    expect(document.body.textContent).toContain('Saved under an older version of this page');
    await click(row);
    expect(applySnapshot).not.toHaveBeenCalled();
  });

  it('names a removed author rather than rendering a blank line', async () => {
    apiMock.mockResolvedValue({ views: [view({ created_by: null, created_by_name: null })] });
    render();
    await open();
    expect(document.body.textContent).toContain('by (removed)');
  });
});

/** Answer GETs with a list envelope and every write with the row, so the reload a write
 *  triggers is served correctly — a single `mockResolvedValue` would feed the write's row
 *  back to the list and is what a real server would never do. */
function routeApi(views: ReturnType<typeof view>[], writeResult: unknown = view()) {
  apiMock.mockImplementation((_path: string, init?: RequestInit) =>
    init?.method ? Promise.resolve(writeResult) : Promise.resolve({ views }),
  );
}

describe('saving', () => {
  it('posts the current screen state under the surface’s version', async () => {
    routeApi([]);
    render();
    await open();
    await click(byText('Save current view'));
    await submitName('Closing soon');

    const post = apiMock.mock.calls.find(c => (c[1] as RequestInit | undefined)?.method === 'POST');
    expect(post).toBeDefined();
    expect(JSON.parse(String((post![1] as RequestInit).body))).toEqual({
      surface: 'crm_pipeline',
      name: 'Closing soon',
      version: 2,
      payload: EXPECTED_SNAPSHOT,
    });
    expect(toastMock.success).toHaveBeenCalled();
  });

  it('keeps the form open and shows the message when the name is taken', async () => {
    apiMock.mockResolvedValue({ views: [] });
    render();
    await open();
    await click(byText('Save current view'));
    apiMock.mockRejectedValue(new FakeApiError(409, 'A view named "Q3" already exists on this page.'));
    await submitName('Q3');
    expect(document.body.textContent).toContain('already exists on this page');
    expect(document.querySelector('input[aria-label="View name"]')).not.toBeNull();
    expect(toastMock.error).not.toHaveBeenCalled();
  });
});

describe('editing is gated on the server’s can_edit', () => {
  it('offers no edit actions on someone else’s view', async () => {
    apiMock.mockResolvedValue({ views: [view({ can_edit: false })] });
    render();
    await open();
    const labels = buttons().map(b => b.textContent?.trim());
    expect(labels).not.toContain('Rename');
    expect(labels).not.toContain('Delete');
    expect(labels).not.toContain('Update to current view');
  });

  it('renames through PUT with only the name', async () => {
    routeApi([view()], view({ name: 'Q4 pipeline' }));
    render();
    await open();
    await click(byText('Rename'));
    await submitName('Q4 pipeline');
    const put = apiMock.mock.calls.find(c => (c[1] as RequestInit | undefined)?.method === 'PUT');
    expect(put![0]).toBe('/api/saved-views/1');
    expect(JSON.parse(String((put![1] as RequestInit).body))).toEqual({ name: 'Q4 pipeline' });
  });

  it('overwrites with the payload AND its version, after confirming', async () => {
    routeApi([view()]);
    render();
    await open();
    confirmMock.mockResolvedValue(true);
    await click(byText('Update to current view'));
    const put = apiMock.mock.calls.find(c => (c[1] as RequestInit | undefined)?.method === 'PUT');
    expect(JSON.parse(String((put![1] as RequestInit).body))).toEqual({
      payload: EXPECTED_SNAPSHOT,
      version: 2,
    });
  });

  it('deletes only after the confirm resolves true', async () => {
    routeApi([view()], { ok: true });
    render();
    await open();

    confirmMock.mockResolvedValue(false);
    await click(byText('Delete'));
    expect(apiMock.mock.calls.some(c => (c[1] as RequestInit | undefined)?.method === 'DELETE'))
      .toBe(false);

    confirmMock.mockResolvedValue(true);
    await click(byText('Delete'));
    const del = apiMock.mock.calls.find(c => (c[1] as RequestInit | undefined)?.method === 'DELETE');
    expect(del![0]).toBe('/api/saved-views/1');
  });

  it('leaves another row’s open rename form alone when a delete succeeds', async () => {
    // `mode` and `formError` are single pieces of state shared by every row. A write that
    // opens no form must not reset them, or confirming a delete on one row silently discards
    // the name already typed into a rename form open on another.
    routeApi([view(), view({ id: 2, name: 'Other' })], { ok: true });
    render();
    await open();
    await click(byText('Rename'));
    const field = document.querySelector<HTMLInputElement>('input[aria-label="View name"]')!;
    setInputValue(field, 'Half-typed name');

    confirmMock.mockResolvedValue(true);
    const deletes = buttons().filter(b => b.textContent?.trim() === 'Delete');
    await click(deletes[deletes.length - 1]);

    const stillOpen = document.querySelector<HTMLInputElement>('input[aria-label="View name"]');
    expect(stillOpen).not.toBeNull();
    expect(stillOpen!.value).toBe('Half-typed name');
  });

  it('toasts a non-conflict failure instead of trapping it in a form', async () => {
    apiMock.mockResolvedValue({ views: [view()] });
    render();
    await open();
    confirmMock.mockResolvedValue(true);
    apiMock.mockRejectedValue(new FakeApiError(403, 'Only the view’s creator or an admin can delete it'));
    await click(byText('Delete'));
    expect(toastMock.error).toHaveBeenCalledWith(
      'Only the view’s creator or an admin can delete it',
    );
  });
});

describe('dismissal', () => {
  it('closes on Escape', async () => {
    apiMock.mockResolvedValue({ views: [view()] });
    render();
    await open();
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    await act(async () => {
      document
        .querySelector('[role="dialog"]')!
        .dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });

  it('closes on a click outside', async () => {
    apiMock.mockResolvedValue({ views: [view()] });
    render();
    await open();
    await act(async () => {
      document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    });
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });
});
