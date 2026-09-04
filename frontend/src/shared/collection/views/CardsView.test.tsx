// @vitest-environment jsdom
//
// The card grid: sections in first-appearance order with counts, per-section
// caps expanding through the hook, the thumb/badge slots receiving the app item, voided
// styling, and card click → onSelect.
import { StrictMode, act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import useCollectionState from '../useCollectionState';
import CardsView from './CardsView';
import type { CollectionConfig, CollectionState } from '../types';

interface Row {
  id: number;
  name: string;
  section: string;
  voided: boolean;
}

const rows: Row[] = [
  { id: 1, name: 'one', section: 'Open', voided: false },
  { id: 2, name: 'two', section: 'Done', voided: false },
  { id: 3, name: 'three', section: 'Open', voided: true },
  { id: 4, name: 'four', section: 'Open', voided: false },
];

function makeConfig(key: string, sectionCap?: number): CollectionConfig<Row> {
  return {
    storage: { key, version: 1 },
    defaultView: 'cards',
    getItemId: r => r.id,
    searchText: r => [r.name],
    cards: { getTitle: r => r.name, getSection: r => r.section, sectionCap },
    getVoided: r => r.voided,
  };
}

let container: HTMLDivElement;
let root: Root;
const latest: { current: CollectionState<Row> | null } = { current: null };
const onSelect = vi.fn();

function Page({ config, noSelect = false }: { config: CollectionConfig<Row>; noSelect?: boolean }) {
  const state = useCollectionState(config, rows);
  useEffect(() => {
    latest.current = state;
  });
  return (
    <CardsView
      config={config}
      state={state}
      cards={{ renderBadge: item => <em data-testid="badge">{item.section}</em> }}
      onSelect={noSelect ? undefined : onSelect}
    />
  );
}

function renderPage(config: CollectionConfig<Row>, noSelect = false): void {
  act(() => {
    root.render(
      <StrictMode>
        <Page config={config} noSelect={noSelect} />
      </StrictMode>,
    );
  });
}

beforeEach(() => {
  sessionStorage.clear();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  latest.current = null;
  onSelect.mockClear();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('CardsView', () => {
  it('renders sections in first-appearance order with counts, badges, and voided strike', () => {
    renderPage(makeConfig('base'));
    const headers = [...document.querySelectorAll('h3')].map(h =>
      (h.textContent ?? '').replace(/\s+/g, ' ').trim(),
    );
    expect(headers).toEqual(['Open (3)', 'Done (1)']);
    expect(document.querySelectorAll('[data-testid="badge"]')).toHaveLength(4);
    const struck = [...document.querySelectorAll('button')].filter(b =>
      (b.querySelector('.line-through') ? true : false),
    );
    expect(struck).toHaveLength(1);
    expect(struck[0].textContent).toContain('three');
  });

  it('keeps the title shrinkable so a long token cannot push the badge out of the card', () => {
    // A flex item defaults to `min-width: auto`, so without `min-w-0` a title holding one
    // long unbreakable token refuses to shrink, drives the `shrink-0` badge past the card's
    // content box, and the card's `overflow-hidden` clips the badge — measured at 18 of 210 cards
    // on a sibling surface's board, the first adopter to default to this view.
    //
    // What this test DOES prove: the three classes that constitute the fix are still rendered, so
    // a future tidy-up of the className strings fails here rather than silently in production.
    // What it CANNOT prove: the visual outcome. jsdom has no layout engine and no Tailwind
    // stylesheet, so overflow is unmeasurable here; that half was verified in a real browser by
    // stripping the classes from the live DOM and watching 0 → 18 cards overflow.
    renderPage(makeConfig('shrink'));
    const card = document.querySelector('section button');
    const title = card?.querySelector('span.font-heading');
    const badgeWrapper = card?.querySelector('[data-testid="badge"]')?.parentElement;

    expect(title?.className).toContain('min-w-0');
    expect(title?.className).toContain('break-words');
    expect(badgeWrapper?.className).toContain('shrink-0');
  });

  it('caps a section and expands through the hook', () => {
    renderPage(makeConfig('cap', 2));
    const cardTitles = () =>
      [...document.querySelectorAll('section')]
        .find(s => s.textContent?.includes('Open'))
        ?.querySelectorAll('button[aria-current], button:not([aria-current])');
    // Open holds 3 cards, capped to 2 + "Show 1 more".
    const showMore = [...document.querySelectorAll('button')].find(
      b => b.textContent === 'Show 1 more',
    );
    expect(showMore).toBeTruthy();
    act(() => (showMore as HTMLElement).click());
    expect(
      [...document.querySelectorAll('button')].some(b => b.textContent === 'Show 1 more'),
    ).toBe(false);
    expect(cardTitles()).toBeTruthy();
  });

  it('card click reports the item id', () => {
    renderPage(makeConfig('click'));
    const card = [...document.querySelectorAll('button')].find(b =>
      b.textContent?.includes('two'),
    ) as HTMLElement;
    act(() => card.click());
    expect(onSelect).toHaveBeenCalledWith(2);
  });

  it('renderCard override owns the whole cell and the wrapper does not call onSelect', () => {
    function CustomPage({ config }: { config: CollectionConfig<Row> }) {
      const state = useCollectionState(config, rows);
      return (
        <CardsView
          config={config}
          state={state}
          cards={{
            renderCard: item => (
              <div data-testid="custom-cell">
                <button type="button" onClick={() => onSelect(item.id)}>enlarge {item.name}</button>
              </div>
            ),
          }}
          onSelect={onSelect}
        />
      );
    }
    act(() => {
      root.render(
        <StrictMode>
          <CustomPage config={makeConfig('render_card')} />
        </StrictMode>,
      );
    });
    // The app's cell renders (once per row), not the built-in title/badge button.
    expect(document.querySelectorAll('[data-testid="custom-cell"]')).toHaveLength(4);
    expect(document.querySelectorAll('[data-testid="badge"]')).toHaveLength(0);
    // The layer's wrapper is a plain <div>, so it doesn't intercept clicks — the app's own
    // button inside the cell is the only thing that calls onSelect.
    const enlarge = [...document.querySelectorAll('button')].find(b =>
      b.textContent?.includes('enlarge two'),
    ) as HTMLElement;
    act(() => enlarge.click());
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith(2);
  });
});

describe('cards with nothing to open (#148)', () => {
  it('renders an inert card — no button, no hover affordance — when the page wires no onSelect', () => {
    // The rule the list view adopted in #148, applied to its sibling: a focusable card that
    // does nothing is worse than no affordance. Rendering the <button> unconditionally is
    // what made that reachable here.
    renderPage(makeConfig('cards_inert'), true);
    const cards = [...document.querySelectorAll('[data-testid="badge"]')].map(
      b => b.closest('div,button')!.closest('button'),
    );
    expect(cards.every(c => c === null)).toBe(true);
    // The content is still fully rendered — inert means not interactive, not hidden.
    expect(document.querySelectorAll('[data-testid="badge"]').length).toBeGreaterThan(0);
    expect(document.body.textContent).toContain('one');
  });

  it('still renders the interactive card when onSelect IS wired', () => {
    renderPage(makeConfig('cards_active'));
    const badge = document.querySelector('[data-testid="badge"]')!;
    const card = badge.closest('button');
    expect(card).not.toBeNull();
    act(() => (card as HTMLElement).click());
    expect(onSelect).toHaveBeenCalled();
  });
});
