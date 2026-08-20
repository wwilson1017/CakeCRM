// @vitest-environment jsdom
//
// House precedent is that the pure module carries the test weight and a render component
// carries none. `SearchFilterBar` is mostly chrome and mostly obeys that — but it also holds
// one guarantee its own docstring singles out, which is pure logic wearing a component's
// clothes:
//
//   "A selected value with no matching option still renders, labelled by its raw value. A chip
//    that silently disappears because its option went away looks exactly like a bug, and leaves
//    the user filtered by something they cannot see or clear."
//
// That is a one-character regression away at all times (`?? String(value)` → `?? ''`), and no
// other test in the repo would notice. The collapsed-chip row is in the same category: it is
// the capability that keeps CRM at parity after its labelled facet buttons and removable pill
// row were replaced by a single "Filters (N)" disclosure, so a board that shows only a count is
// a real regression on a surface reps use daily.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SearchFilterBar from './SearchFilterBar';
import type { FacetGroup } from './types';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let container: HTMLDivElement | null = null;

function render(groups: FacetGroup[], onToggle = vi.fn()) {
  container = document.createElement('div');
  document.body.appendChild(container);
  const withToggle = groups.map(g => ({ ...g, onToggle: g.onToggle ?? onToggle }));
  act(() => {
    root = createRoot(container!);
    root.render(
      <SearchFilterBar
        query=""
        onQueryChange={() => {}}
        groups={withToggle}
        active
        activeFacetCount={withToggle.reduce((n, g) => n + g.selected.length, 0)}
        onClear={() => {}}
      />,
    );
  });
  return container!;
}

afterEach(() => {
  if (root) act(() => root!.unmount());
  container?.remove();
  root = null;
  container = null;
});

const stageGroup = (selected: (string | number)[]): FacetGroup => ({
  key: 'stage',
  label: 'Stage',
  options: [
    { value: 1, label: 'Qualified' },
    { value: 2, label: 'Proposal' },
  ],
  selected,
  onToggle: vi.fn(),
});

describe('SearchFilterBar — active filters while the panel is collapsed', () => {
  it('shows WHICH filters are on, not just how many', () => {
    const el = render([stageGroup([1])]);
    // The disclosure is closed, so the panel's own chips are not mounted at all.
    expect(el.textContent).toContain('Filters (1)');
    expect(el.textContent).toContain('Qualified');
  });

  it('renders a selected value that has no matching option, labelled by its raw value', () => {
    // A stage that was removed (or renamed) after the filter was persisted. Dropping the chip
    // would leave the board filtered by something the user cannot see or clear.
    const el = render([stageGroup([99])]);
    expect(el.textContent).toContain('99');
    expect(el.textContent).toContain('Filters (1)');
  });

  it('lets the user remove a filter straight from the collapsed row', () => {
    const onToggle = vi.fn();
    const el = render([{ ...stageGroup([1]), onToggle }]);
    const remove = el.querySelector('button[aria-label="Remove Stage filter Qualified"]') as HTMLButtonElement;
    expect(remove).not.toBeNull();
    act(() => remove.click());
    expect(onToggle).toHaveBeenCalledWith(1);
  });

  it('renders no chip row at all when nothing is selected', () => {
    const el = render([stageGroup([])]);
    expect(el.textContent).not.toContain('Qualified');
    expect(el.textContent).toContain('Filters');
  });
});

describe('SearchFilterBar — disabled facet options', () => {
  const disabledGroup = (selected: (string | number)[]): FacetGroup => ({
    key: 'stage',
    label: 'Stage',
    options: [
      { value: 1, label: 'Open' },
      { value: 2, label: 'Closed', disabled: true, disabledReason: 'Turn on Show closed' },
    ],
    selected,
    onToggle: vi.fn(),
  });

  const openPanel = (el: HTMLElement) => {
    const disclosure = Array.from(el.querySelectorAll('button')).find(
      b => b.textContent?.includes('Filters'),
    ) as HTMLButtonElement;
    act(() => disclosure.click());
  };

  it('renders an unselected disabled option as disabled, with its reason as a title', () => {
    const el = render([disabledGroup([])]);
    openPanel(el);
    const closed = Array.from(el.querySelectorAll('button')).find(
      b => b.textContent === 'Closed',
    ) as HTMLButtonElement;
    expect(closed.disabled).toBe(true);
    expect(closed.title).toBe('Turn on Show closed');
  });

  it('keeps a SELECTED disabled option clickable so the filter can be undone (P1-1)', () => {
    const onToggle = vi.fn();
    const el = render([{ ...disabledGroup([2]), onToggle }]);
    openPanel(el);
    const closed = Array.from(el.querySelectorAll('button')).find(
      b => b.textContent === 'Closed',
    ) as HTMLButtonElement;
    expect(closed.disabled).toBe(false);
    act(() => closed.click());
    expect(onToggle).toHaveBeenCalledWith(2);
  });
});

describe('SearchFilterBar — high-cardinality facets', () => {
  it('does not mount a list group’s rows until that group is expanded', () => {
    // Kanban has four of these, each with hundreds of options; mounting them all at once is a
    // thousand-plus checkbox nodes on a low-powered tablet.
    const big: FacetGroup = {
      key: 'suppliers',
      label: 'Suppliers',
      display: 'list',
      options: Array.from({ length: 200 }, (_, i) => ({ value: `s${i}`, label: `Supplier ${i}` })),
      selected: [],
      onToggle: vi.fn(),
    };
    const el = render([big]);
    // Open the disclosure panel; the group itself is still collapsed inside it.
    const disclosure = el.querySelector('button[aria-expanded="false"]') as HTMLButtonElement;
    act(() => disclosure.click());
    expect(el.querySelectorAll('input[type="checkbox"]').length).toBe(0);

    // Expand the group — now, and only now, the rows exist.
    const groupToggle = Array.from(el.querySelectorAll('button')).find(b => b.textContent?.includes('Suppliers')) as HTMLButtonElement;
    act(() => groupToggle.click());
    expect(el.querySelectorAll('input[type="checkbox"]').length).toBe(200);
  });
});
