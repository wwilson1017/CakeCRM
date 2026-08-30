// @vitest-environment jsdom
//
// The picker replaces a <select> that had every option in the DOM, so the behaviours worth
// pinning are the ones that only exist now: what the server is asked, when `Create "…"` is
// offered, and what a keystroke means inside a form. The last one is not cosmetic — this
// input lives in <form>, where a bare Enter submits the deal.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { RecordCombobox } from './RecordCombobox';

interface Rec { id: number; name: string }

const ACME: Rec = { id: 1, name: 'Acme Corp' };

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.useFakeTimers();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

function mount(over: Partial<Parameters<typeof RecordCombobox<Rec>>[0]> = {}) {
  const props = {
    label: 'Company',
    value: null as number | null,
    valueLabel: '',
    emptyLabel: 'No company',
    search: vi.fn(async () => [] as Rec[]),
    create: vi.fn(async (name: string) => ({ id: 99, name })),
    getId: (r: Rec) => r.id,
    getLabel: (r: Rec) => r.name,
    onSelect: vi.fn(),
    ...over,
  };
  act(() => { root.render(<RecordCombobox<Rec> {...props} />); });
  return props;
}

function input(): HTMLInputElement {
  const el = container.querySelector('input[role="combobox"]');
  if (!el) throw new Error('no combobox rendered');
  return el as HTMLInputElement;
}

/** Settle the 250ms debounce AND the promise chain the effect starts after it. */
async function settle(ms = 300) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

async function open() {
  await act(async () => {
    input().dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await settle();
}

/** Type the way React can see. Assigning `.value` directly is invisible to it: React caches
 *  the last value on the node, sees no change, and never re-runs onChange. */
async function type(value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  await act(async () => {
    setter?.call(input(), value);
    input().dispatchEvent(new Event('input', { bubbles: true }));
  });
}

function rowLabels(): string[] {
  return [...container.querySelectorAll('[role="option"]')].map(o => o.textContent || '');
}

/** The Create row. `role="option"` is a leaf in the accessibility tree, so the row IS the
 *  option element — there is deliberately no nested <button> to click. */
function createRow(): HTMLElement | undefined {
  return [...container.querySelectorAll('[role="option"]')]
    .find(o => o.textContent?.startsWith('Create ')) as HTMLElement | undefined;
}

/** Dispatch a cancelable keydown and report whether the handler called preventDefault. */
async function press(key: string): Promise<boolean> {
  const ev = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
  await act(async () => { input().dispatchEvent(ev); });
  return ev.defaultPrevented;
}

describe('RecordCombobox — searching', () => {
  it('queries the server with the TRIMMED text, so stray whitespace still finds the record', async () => {
    // The whole leading/trailing-whitespace duplicate hole closes here: " Acme Corp "
    // searching as "Acme Corp" is what surfaces the existing company, which is in turn
    // what stops `Create "…"` from being offered for a company that already exists.
    const props = mount({ search: vi.fn(async () => [ACME]) });
    await open();
    await type('  Acme Corp  ');
    await settle();

    expect(props.search).toHaveBeenCalledWith('Acme Corp');
  });

  it('drops a slow earlier response so it cannot overwrite a newer one', async () => {
    // Typing "ac" then "acme" can settle out of order; without the request-id guard the
    // list ends up showing matches for a query the user has already moved past.
    let releaseFirst: (rows: Rec[]) => void = () => {};
    const search = vi.fn()
      .mockImplementationOnce(() => new Promise<Rec[]>(res => { releaseFirst = res; }))
      .mockImplementation(async () => [ACME]);
    mount({ search });

    await open();          // request 1 — left hanging
    await type('acme');
    await settle();        // request 2 — resolves with Acme Corp
    await act(async () => { releaseFirst([{ id: 42, name: 'STALE' }]); });

    expect(rowLabels().some(l => l.includes('STALE'))).toBe(false);
  });
});

describe('RecordCombobox — quick create', () => {
  it('offers Create "<name>" when nothing matches', async () => {
    mount({ search: vi.fn(async () => []) });
    await open();
    await type('Newco');
    await settle();

    expect(createRow()?.textContent).toBe('Create "Newco"…');
  });

  it('does NOT offer Create when a result matches case-insensitively', async () => {
    // Otherwise the picker invites you to create a duplicate of the row directly above it.
    mount({ search: vi.fn(async () => [ACME]) });
    await open();
    await type('acme corp');
    await settle();

    expect(rowLabels()).toEqual(['Acme Corp']);
    expect(createRow()).toBeUndefined();
  });

  it('does not offer Create while the search for the typed text is still in flight', async () => {
    // The stale-results flash: "Acm" returning nothing must not make `Create "Acme"` appear
    // before the search for "Acme" has run — the user would create a company that exists.
    mount({ search: vi.fn(async () => []) });
    await open();
    await type('Acme');            // debounce not yet elapsed

    expect(createRow()).toBeUndefined();
  });

  it('creates with the trimmed name and selects the new record', async () => {
    const props = mount({ search: vi.fn(async () => []) });
    await open();
    await type('  Newco  ');
    await settle();
    await act(async () => { createRow()!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });

    expect(props.create).toHaveBeenCalledWith('Newco');
    expect(props.onSelect).toHaveBeenCalledWith({ id: 99, name: 'Newco' });
  });

  it('reports a failed create inline instead of selecting nothing', async () => {
    const props = mount({
      search: vi.fn(async () => []),
      create: vi.fn(async () => { throw new Error('API error 409: Could not resolve that company'); }),
    });
    await open();
    await type('Newco');
    await settle();
    await act(async () => { createRow()!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });

    expect(props.onSelect).not.toHaveBeenCalled();
    // The transport prefix is stripped — the user reads the reason, not the status line.
    expect(container.textContent).toContain('Could not resolve that company');
  });
});

describe('RecordCombobox — keyboard', () => {
  it('swallows Enter while the list is open, so picking a row cannot submit the form', async () => {
    // This input sits inside <form>. A bare Enter would submit the deal instead of choosing
    // the row the user is looking at — asserted on defaultPrevented because jsdom does not
    // implement implicit form submission, so watching for a submit event would prove nothing.
    const props = mount({ search: vi.fn(async () => [ACME]) });
    await open();

    expect(await press('Enter')).toBe(true);
    expect(props.onSelect).toHaveBeenCalledWith(ACME);
  });

  it('leaves Enter alone when the list is closed', async () => {
    mount();

    expect(await press('Enter')).toBe(false);
  });

  it('Escape closes the list without clearing the selection', async () => {
    const props = mount({ value: 1, valueLabel: 'Acme Corp', search: vi.fn(async () => [ACME]) });
    await open();
    await press('Escape');

    expect(container.querySelector('[role="listbox"]')).toBeNull();
    expect(props.onSelect).not.toHaveBeenCalled();
  });

  it('moves the active row with ArrowDown and wraps at the end', async () => {
    const rows: Rec[] = [ACME, { id: 2, name: 'Beta Ltd' }];
    const props = mount({ search: vi.fn(async () => rows) });
    await open();
    await press('ArrowDown');
    await press('ArrowDown');   // wraps back to the first row

    expect(await press('Enter')).toBe(true);
    expect(props.onSelect).toHaveBeenCalledWith(ACME);
  });
});

describe('RecordCombobox — the linked record', () => {
  it('displays a linked record the search never returns', async () => {
    // The capped-page hazard this component exists to end: the old <select> had no <option>
    // for an out-of-page link, so it rendered blank and read as "none".
    mount({ value: 4242, valueLabel: 'Very Old Company Ltd', search: vi.fn(async () => []) });

    expect(input().value).toBe('Very Old Company Ltd');
  });

  it('clears the link when the clear button is pressed', async () => {
    const props = mount({ value: 1, valueLabel: 'Acme Corp' });
    const clear = container.querySelector('button[aria-label="Clear company"]') as HTMLButtonElement;
    await act(async () => { clear.dispatchEvent(new MouseEvent('click', { bubbles: true })); });

    expect(props.onSelect).toHaveBeenCalledWith(null);
  });
});

describe('RecordCombobox — a failed search', () => {
  it('says the search failed instead of claiming there were no matches', async () => {
    // "No matches" for a request that ERRORED states as fact the one thing we do not know,
    // and for the contact picker — whose create path has no uniqueness constraint behind
    // it — that is how a duplicate gets made.
    mount({ search: vi.fn(async () => { throw new Error('network'); }) });
    await open();

    expect(container.textContent).toContain('Search failed');
    expect(container.textContent).not.toContain('No matches');
  });

  it('still offers Create after a failed search, rather than stranding the user', async () => {
    mount({ search: vi.fn(async () => { throw new Error('network'); }) });
    await open();
    await type('Newco');
    await settle();

    expect(createRow()?.textContent).toBe('Create "Newco"…');
  });

  it('settles out of the loading state on failure', async () => {
    // A rejected search must not leave the list stuck on "Searching…" forever.
    mount({ search: vi.fn(async () => { throw new Error('network'); }) });
    await open();

    expect(container.textContent).not.toContain('Searching');
  });

  it('clears the failed state once a later search succeeds', async () => {
    const search = vi.fn()
      .mockImplementationOnce(async () => { throw new Error('network'); })
      .mockImplementation(async () => [ACME]);
    mount({ search });
    await open();
    await type('Acme');
    await settle();

    expect(container.textContent).not.toContain('Search failed');
  });
});

describe('RecordCombobox — accessibility wiring', () => {
  it('points aria-activedescendant at the active row and moves it with the keyboard', async () => {
    // A screen-reader user has nothing but this attribute to know which row is active, so
    // it has to track `active` — a behavioural test on which record gets picked would not
    // notice it drifting.
    mount({ search: vi.fn(async () => [ACME, { id: 2, name: 'Beta Ltd' }]) });
    await open();

    const first = input().getAttribute('aria-activedescendant');
    expect(first).toBeTruthy();
    await press('ArrowDown');
    const second = input().getAttribute('aria-activedescendant');
    expect(second).not.toBe(first);
    // ...and it names the row the list actually marks as selected.
    expect(document.getElementById(second!)?.getAttribute('aria-selected')).toBe('true');
  });

  it('reports its expanded state on the combobox itself', async () => {
    mount({ search: vi.fn(async () => [ACME]) });

    expect(input().getAttribute('aria-expanded')).toBe('false');
    await open();
    expect(input().getAttribute('aria-expanded')).toBe('true');
  });
});

describe('RecordCombobox — dismissal', () => {
  it('closes on a click outside without selecting anything', async () => {
    // Otherwise the popover sits open over the rest of the form.
    const props = mount({ search: vi.fn(async () => [ACME]) });
    await open();
    await act(async () => {
      document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    });

    expect(container.querySelector('[role="listbox"]')).toBeNull();
    expect(props.onSelect).not.toHaveBeenCalled();
  });
});

describe('RecordCombobox — a create that outlives the user\'s intent', () => {
  it('does not select a slow create the user has already moved past', async () => {
    // The search's request-id guard does not cover creates. Without its own token, a create
    // that resolves after the user dismissed the list still selects its record, silently
    // replacing whatever they chose instead.
    let release: (r: Rec) => void = () => {};
    const props = mount({
      search: vi.fn(async () => []),
      create: vi.fn(() => new Promise<Rec>(res => { release = res; })),
    });
    await open();
    await type('Newco');
    await settle();
    await act(async () => { createRow()!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    await press('Escape');                                  // the user moves on
    await act(async () => { release({ id: 99, name: 'Newco' }); });

    expect(props.onSelect).not.toHaveBeenCalled();
  });

  it('still selects a create nobody interrupted', async () => {
    // The guard above must not swallow the ordinary case.
    let release: (r: Rec) => void = () => {};
    const props = mount({
      search: vi.fn(async () => []),
      create: vi.fn(() => new Promise<Rec>(res => { release = res; })),
    });
    await open();
    await type('Newco');
    await settle();
    await act(async () => { createRow()!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    await act(async () => { release({ id: 99, name: 'Newco' }); });

    expect(props.onSelect).toHaveBeenCalledWith({ id: 99, name: 'Newco' });
  });
});

describe('RecordCombobox — text entry that is not navigation', () => {
  it('ignores Enter while an IME is composing', async () => {
    // This Enter commits the candidate being typed, not a row in our list. Selecting on it
    // replaces what the user was writing — routine for CJK input, invisible on a Latin
    // keyboard, which is exactly why it needs a test.
    const props = mount({ search: vi.fn(async () => [ACME]) });
    await open();

    const ev = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
    Object.defineProperty(ev, 'isComposing', { value: true });
    await act(async () => { input().dispatchEvent(ev); });

    expect(props.onSelect).not.toHaveBeenCalled();
  });

  it('closes the list on Tab so it cannot cover the next field', async () => {
    // Focus is leaving; a popover left open sits over the rest of the form, and Escape from
    // the newly focused control cannot reach it.
    const props = mount({ search: vi.fn(async () => [ACME]) });
    await open();
    await press('Tab');

    expect(container.querySelector('[role="listbox"]')).toBeNull();
    expect(props.onSelect).not.toHaveBeenCalled();
  });
});

describe('RecordCombobox — reopening', () => {
  it('does not search the query the user already left behind', async () => {
    // `useDebounce` cannot be reset, so after openList clears the query the debounced value
    // still holds the old text for 250ms — reopening would search it, then search "".
    const search = vi.fn(async (q: string) => (q ? [ACME] : []));
    mount({ search });
    await open();
    await type('Acme');
    await settle();
    await press('Escape');
    search.mockClear();

    await open();

    expect(search.mock.calls.every(([q]) => q === '')).toBe(true);
  });
});

describe('RecordCombobox — recovering from an interrupted create', () => {
  it('re-enables Create after an interrupted one, instead of wedging on "Creating…"', async () => {
    // The superseded branch RETURNS, so releasing `creating` only on the fall-through path
    // leaves the row disabled for the rest of the form's life.
    let release: (r: Rec) => void = () => {};
    mount({
      search: vi.fn(async () => []),
      create: vi.fn(() => new Promise<Rec>(res => { release = res; })),
    });
    await open();
    await type('Newco');
    await settle();
    await act(async () => { createRow()!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    await press('Escape');
    await act(async () => { release({ id: 99, name: 'Newco' }); });

    await open();
    await type('Newco');
    await settle();
    expect(createRow()!.getAttribute('aria-disabled')).toBe('false');
  });

  it('does not let a create land on a query the user has since retyped', async () => {
    let release: (r: Rec) => void = () => {};
    const props = mount({
      search: vi.fn(async () => []),
      create: vi.fn(() => new Promise<Rec>(res => { release = res; })),
    });
    await open();
    await type('Alpha');
    await settle();
    await act(async () => { createRow()!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    await type('Beta');                                      // still typing; create in flight
    await act(async () => { release({ id: 99, name: 'Alpha' }); });

    expect(props.onSelect).not.toHaveBeenCalled();
  });
});

describe('RecordCombobox — a superseded create that fails', () => {
  it('does not report a stale create error against the query the user has moved to', async () => {
    let reject: (e: Error) => void = () => {};
    mount({
      search: vi.fn(async () => []),
      create: vi.fn(() => new Promise<Rec>((_res, rej) => { reject = rej; })),
    });
    await open();
    await type('Alpha');
    await settle();
    await act(async () => { createRow()!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    await type('Beta');
    await act(async () => { reject(new Error('API error 500: Alpha blew up')); });

    expect(container.textContent).not.toContain('Alpha blew up');
  });
});

describe('RecordCombobox — the listbox contract', () => {
  it('keeps every option a leaf, with no interactive descendants', async () => {
    // `role="option"` is a leaf in the accessibility tree: a nested <button> makes screen
    // readers announce and activate the row unreliably, and axe flags it. Pinned because the
    // obvious way to write a clickable row is exactly the wrong one.
    mount({ search: vi.fn(async () => [ACME]) });
    await open();
    await type('Newco');
    await settle();

    const options = [...container.querySelectorAll('[role="option"]')];
    expect(options.length).toBeGreaterThan(0);
    for (const option of options) {
      expect(option.querySelector('button, a, input, select, textarea')).toBeNull();
    }
  });

  it('does not point aria-controls at a list that is not mounted', async () => {
    // A dangling idref is an error to assistive tech, and the list only exists while open.
    mount();

    expect(input().getAttribute('aria-controls')).toBeNull();
    await open();
    expect(input().getAttribute('aria-controls')).toBeTruthy();
  });

  it('links its label to its input even when no id prop is given', async () => {
    // The `id` prop is optional, and #126 reuses this component — rendering `undefined`
    // would silently drop click-to-focus and the input's accessible name.
    mount();

    const label = container.querySelector('label')!;
    expect(label.getAttribute('for')).toBeTruthy();
    expect(label.getAttribute('for')).toBe(input().id);
  });
});
