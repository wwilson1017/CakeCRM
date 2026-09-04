// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react';
import KanbanBoard from './KanbanBoard';
import type { KanbanItem } from './types';

/**
 * The machine contract between `KanbanBoard` and `collision.ts`, pinned (issue #147).
 *
 * `collision.ts`'s `boardVisibleBox` finds the board's scroll region by walking UP from any
 * measured droppable's own node, via `node.closest('[data-kanban-scroller]')`. Nothing else in
 * the app reads that attribute, so a rename on either side would break hit-testing SILENTLY —
 * `boardVisibleBox` would simply return `null`, every card would go unclipped, and cards
 * scrolled past the board's horizontal fold would quietly become drop targets again. This test
 * exercises a REAL DOM `closest()` call against the string literal `KanbanBoard` actually
 * renders, so that rename breaks a test instead.
 *
 * It also proves the attribute sits on the same node `scrollerRef` forwards, since the pipeline
 * board uses that ref for its mobile scroll-snap sync — a board that marked one element and
 * forwarded another would clip against the wrong box.
 */
interface Item extends KanbanItem {
  title: string;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('KanbanBoard', () => {
  it('marks its scroll region with data-kanban-scroller and forwards it through scrollerRef', () => {
    const scrollerRef: { current: HTMLDivElement | null } = { current: null };

    act(() => {
      root.render(
        <KanbanBoard<Item, { name: string }>
          columns={[
            { id: 'todo', data: { name: 'Todo' } },
            { id: 'done', data: { name: 'Done' } },
          ]}
          items={{
            todo: [{ id: 1, title: 'One' }],
            done: [{ id: 2, title: 'Two' }],
          }}
          onMove={async () => {}}
          renderColumn={(col, children) => <div key={col.id}>{children}</div>}
          renderCard={item => <div data-card={item.id}>{item.title}</div>}
          className="flex"
          scrollerRef={scrollerRef}
        />,
      );
    });

    expect(scrollerRef.current).toBeInstanceOf(HTMLElement);
    expect(scrollerRef.current!.matches('[data-kanban-scroller]')).toBe(true);

    // A card's droppable node must resolve UP to that same element — the exact walk
    // `boardVisibleBox` performs at collision time.
    const card = container.querySelector('[data-card="1"]') as HTMLElement | null;
    expect(card).toBeInstanceOf(HTMLElement);
    expect(card!.closest('[data-kanban-scroller]')).toBe(scrollerRef.current);
  });

  it('marks the scroll region even when no scrollerRef is supplied', () => {
    // `scrollerRef` is optional and the collection layer's KanbanView passes it through
    // unset. The attribute is what `collision.ts` needs, so it must not ride on the ref.
    act(() => {
      root.render(
        <KanbanBoard<Item, { name: string }>
          columns={[{ id: 'todo', data: { name: 'Todo' } }]}
          items={{ todo: [{ id: 1, title: 'One' }] }}
          onMove={async () => {}}
          renderColumn={(col, children) => <div key={col.id}>{children}</div>}
          renderCard={item => <div data-card={item.id}>{item.title}</div>}
          className="flex"
        />,
      );
    });

    const card = container.querySelector('[data-card="1"]') as HTMLElement | null;
    expect(card!.closest('[data-kanban-scroller]')).toBeInstanceOf(HTMLElement);
  });
});
