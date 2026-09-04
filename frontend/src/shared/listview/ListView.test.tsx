// @vitest-environment jsdom
//
// Behavioural proofs for the shared list view.
//
// WHY THIS FILE EXISTS. Three of ListView's properties are invisible to both
// `tsc` and eslint and would look fine in a screenshot: that a header click
// actually REORDERS rows (rather than only flipping a glyph), that `aria-sort`
// tracks the active column (the repo's first use of it — a wrong value is worse
// than none), and that the render cap re-arms when the result set changes. The
// cap reset in particular is a render-time derived-state adjustment, which is
// exactly the kind of thing that silently stops working.
//
// Follows the repo's jsdom idiom (`the blueprint's polling-hook test`):
// a per-file environment docblock plus ~20 lines of `createRoot` + React 19
// `act`, deliberately instead of @testing-library/react.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ListView from './ListView';
import type { ListColumn, SortState } from './types';

interface Row { id: number; name: string; score: number | null }

const COLUMNS: ListColumn<Row>[] = [
  { key: 'name', header: 'Name', render: r => r.name, sortValue: r => r.name },
  { key: 'score', header: 'Score', align: 'right', render: r => r.score ?? '—', sortValue: r => r.score },
  { key: 'note', header: 'Note', render: () => 'n/a' },   // display-only, not sortable
];

// A cell carrying its own interactive control — the shape the collection layer's selection
// checkbox and a CRM list column's inline link/button both take (#148).
const INTERACTIVE_COLUMNS: ListColumn<Row>[] = [
  ...COLUMNS,
  { key: 'action', header: 'Action', render: () => <button type="button">act</button> },
];

const ROWS: Row[] = [
  { id: 1, name: 'charlie', score: 2 },
  { id: 2, name: 'alpha', score: null },
  { id: 3, name: 'bravo', score: 9 },
];

// Every other DOM test in the repo sets this; without it React warns on every
// act() call and its flush guarantees are not the ones the assertions rely on.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement | null = null;
let root: Root | null = null;

function render(ui: React.ReactNode) {
  if (!container) {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  }
  act(() => { root!.render(ui); });
  return container;
}

afterEach(() => {
  act(() => { root?.unmount(); });
  container?.remove();
  container = null;
  root = null;
});

const rowNames = (el: HTMLElement) =>
  Array.from(el.querySelectorAll('tbody tr')).map(tr => tr.querySelector('td')?.textContent).join(',');

const header = (el: HTMLElement, label: string) =>
  Array.from(el.querySelectorAll('th')).find(th => th.textContent?.includes(label))!;

describe('ListView', () => {
  it('renders rows in the given order when unsorted', () => {
    const el = render(<ListView columns={COLUMNS} items={ROWS} onRowClick={() => {}} />);
    expect(rowNames(el)).toBe('charlie,alpha,bravo');
  });

  it('reorders rows by the active column and tracks aria-sort', () => {
    let sort: SortState | null = null;
    const onSortChange = vi.fn((next: SortState | null) => { sort = next; });
    const el = render(<ListView columns={COLUMNS} items={ROWS} sort={sort} onSortChange={onSortChange} onRowClick={() => {}} />);

    // Idle: no aria-sort anywhere.
    expect(header(el, 'Name').getAttribute('aria-sort')).toBeNull();

    act(() => { header(el, 'Name').querySelector('button')!.click(); });
    expect(onSortChange).toHaveBeenCalledWith({ key: 'name', dir: 'asc' });

    // Controlled component: re-render with what the consumer would store.
    render(<ListView columns={COLUMNS} items={ROWS} sort={sort} onSortChange={onSortChange} onRowClick={() => {}} />);
    expect(rowNames(el)).toBe('alpha,bravo,charlie');
    expect(header(el, 'Name').getAttribute('aria-sort')).toBe('ascending');

    act(() => { header(el, 'Name').querySelector('button')!.click(); });
    render(<ListView columns={COLUMNS} items={ROWS} sort={sort} onSortChange={onSortChange} onRowClick={() => {}} />);
    expect(rowNames(el)).toBe('charlie,bravo,alpha');
    expect(header(el, 'Name').getAttribute('aria-sort')).toBe('descending');

    // Third click clears back to natural board order.
    act(() => { header(el, 'Name').querySelector('button')!.click(); });
    expect(onSortChange).toHaveBeenLastCalledWith(null);
  });

  it('keeps a null-valued row at the bottom in both directions', () => {
    const el = render(<ListView columns={COLUMNS} items={ROWS} sort={{ key: 'score', dir: 'asc' }} onSortChange={() => {}} onRowClick={() => {}} />);
    expect(rowNames(el)).toBe('charlie,bravo,alpha');
    render(<ListView columns={COLUMNS} items={ROWS} sort={{ key: 'score', dir: 'desc' }} onSortChange={() => {}} onRowClick={() => {}} />);
    expect(rowNames(el)).toBe('bravo,charlie,alpha');
  });

  it('renders no sort button for a display-only column', () => {
    const el = render(<ListView columns={COLUMNS} items={ROWS} sort={null} onSortChange={() => {}} onRowClick={() => {}} />);
    expect(header(el, 'Note').querySelector('button')).toBeNull();
    expect(header(el, 'Name').querySelector('button')).not.toBeNull();
  });

  it('leaves rows unsorted when the sort key matches no column', () => {
    // A search bar may own a sort field this table has no column for; that must
    // render as natural order rather than throw or blank the list.
    const el = render(<ListView columns={COLUMNS} items={ROWS} sort={{ key: 'nope', dir: 'asc' }} onSortChange={() => {}} onRowClick={() => {}} />);
    expect(rowNames(el)).toBe('charlie,alpha,bravo');
  });

  it('delivers the clicked item', () => {
    const onRowClick = vi.fn();
    const el = render(<ListView columns={COLUMNS} items={ROWS} onRowClick={onRowClick} />);
    act(() => { el.querySelectorAll('tbody tr')[1].dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    expect(onRowClick).toHaveBeenCalledWith(ROWS[1]);
  });

  // #148. A row that only listens for clicks is unreachable by keyboard — a WCAG 2.1.1
  // failure on every collection surface at once. These pin the fix at the seam: the row
  // activates from the keyboard, a keystroke aimed at a control INSIDE it does not reach the
  // row, and a row with nothing to open grows no affordance at all.
  it('opens a focused row with Enter and with Space, and Space prevents scrolling', () => {
    const onRowClick = vi.fn();
    const el = render(<ListView columns={COLUMNS} items={ROWS} onRowClick={onRowClick} />);
    const row = el.querySelectorAll('tbody tr')[1];
    expect(row.getAttribute('tabindex')).toBe('0');

    act(() => { row.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); });
    expect(onRowClick).toHaveBeenCalledWith(ROWS[1]);

    // Space must activate AND be prevented — unprevented it scrolls the page instead.
    const space = new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true });
    act(() => { row.dispatchEvent(space); });
    expect(onRowClick).toHaveBeenCalledTimes(2);
    expect(space.defaultPrevented).toBe(true);
  });

  it('ignores every key other than Enter and Space', () => {
    // Pins the second guard line. Without it, a later feature that adds arrow-key navigation
    // over these rows would silently open a record on every keystroke, across every adopting
    // surface, with nothing red.
    const onRowClick = vi.fn();
    const el = render(<ListView columns={COLUMNS} items={ROWS} onRowClick={onRowClick} />);
    const row = el.querySelectorAll('tbody tr')[0];
    for (const key of ['ArrowDown', 'ArrowUp', 'Escape', 'Tab', 'a']) {
      act(() => { row.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true })); });
    }
    expect(onRowClick).not.toHaveBeenCalled();
  });

  it('ignores a keystroke aimed at a control inside a cell', () => {
    const onRowClick = vi.fn();
    const el = render(<ListView columns={INTERACTIVE_COLUMNS} items={ROWS} onRowClick={onRowClick} />);
    // `td button` so the sortable column headers can never satisfy the query.
    const inner = el.querySelectorAll('tbody tr')[0].querySelector('td button')!;

    act(() => { inner.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); });
    act(() => { inner.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true })); });
    expect(onRowClick).not.toHaveBeenCalled();
  });

  it('renders an inert row — no tab stop, no pointer affordance — when onRowClick is omitted', () => {
    const el = render(<ListView columns={COLUMNS} items={ROWS} />);
    const row = el.querySelectorAll('tbody tr')[0];
    expect(row.getAttribute('tabindex')).toBeNull();
    expect(row.className).not.toContain('cursor-pointer');
    // Neither input path may throw where there is no handler to call.
    act(() => { row.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    act(() => { row.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); });
    expect(row.className).toContain('border-line-faint');
  });

  it('describes interactive tables with a resolvable sr-only hint, and only those', () => {
    // The hint carries real gating (interactive AND non-empty) and a paired attribute-to-element
    // reference — a dangling IDREF announces nothing and is invisible to every other test.
    const el = render(<ListView columns={COLUMNS} items={ROWS} onRowClick={() => {}} />);
    const describedBy = el.querySelector('table')!.getAttribute('aria-describedby')!;
    expect(el.querySelector(`#${CSS.escape(describedBy)}`)?.textContent).toContain(
      'Rows are interactive',
    );

    // Inert table: nothing to open, so nothing to announce.
    render(<ListView columns={COLUMNS} items={ROWS} />);
    expect(el.querySelector('table')!.getAttribute('aria-describedby')).toBeNull();

    // Interactive but empty: the empty state must not claim interactive rows.
    render(<ListView columns={COLUMNS} items={[]} onRowClick={() => {}} />);
    expect(el.querySelector('table')!.getAttribute('aria-describedby')).toBeNull();
  });

  it('renders the empty message', () => {
    const el = render(<ListView columns={COLUMNS} items={[]} onRowClick={() => {}} emptyMessage="No deals match these filters." />);
    expect(el.textContent).toContain('No deals match these filters.');
  });

  it('caps rendered rows, reveals on show all, and re-arms the cap for a new result set', () => {
    const many = Array.from({ length: 5 }, (_, i) => ({ id: i, name: `r${i}`, score: i }));
    const el = render(<ListView columns={COLUMNS} items={many} onRowClick={() => {}} renderCap={2} />);
    expect(el.querySelectorAll('tbody tr')).toHaveLength(2);
    expect(el.textContent).toContain('Showing 2 of 5');

    const showAll = Array.from(el.querySelectorAll('button')).find(b => b.textContent === 'show all')!;
    act(() => { showAll.click(); });
    expect(el.querySelectorAll('tbody tr')).toHaveLength(5);

    // A DIFFERENTLY-SIZED result set puts the cap back in force — "show all"
    // was a decision about one specific result set.
    render(<ListView columns={COLUMNS} items={many.slice(0, 4)} onRowClick={() => {}} renderCap={2} />);
    expect(el.querySelectorAll('tbody tr')).toHaveLength(2);
  });

  it('keeps "show all" across a refetch that returns the same rows', () => {
    // The regression this pins: every board re-derives its rows through a
    // useMemo, so a refetch / single-card patch / rolled-back drag mints a fresh
    // ARRAY holding the same rows. Resetting on identity would silently collapse
    // an expanded list back to the cap and jump the page, with no filter changed.
    const many = Array.from({ length: 5 }, (_, i) => ({ id: i, name: `r${i}`, score: i }));
    const el = render(<ListView columns={COLUMNS} items={many} onRowClick={() => {}} renderCap={2} />);
    act(() => { Array.from(el.querySelectorAll('button')).find(b => b.textContent === 'show all')!.click(); });
    expect(el.querySelectorAll('tbody tr')).toHaveLength(5);

    // Same rows, brand-new array identity.
    render(<ListView columns={COLUMNS} items={many.map(r => ({ ...r }))} onRowClick={() => {}} renderCap={2} />);
    expect(el.querySelectorAll('tbody tr')).toHaveLength(5);
  });
});
