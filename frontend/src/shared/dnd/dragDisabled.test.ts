import { describe, expect, it } from 'vitest';
import { boardDragDisabled, resolveDragDisabled } from './dragDisabled';

interface Row { id: number; archived: boolean }
const live: Row = { id: 1, archived: false };
const archived: Row = { id: 2, archived: true };
const byArchived = (r: Row) => r.archived;

describe('resolveDragDisabled', () => {
  it('passes a board-wide boolean through unchanged', () => {
    expect(resolveDragDisabled(true, live)).toBe(true);
    expect(resolveDragDisabled(false, archived)).toBe(false);
  });

  it('treats an absent policy as draggable', () => {
    expect(resolveDragDisabled(undefined, live)).toBe(false);
  });

  it('consults a predicate per card, so one card can differ from its neighbour', () => {
    expect(resolveDragDisabled(byArchived, live)).toBe(false);
    expect(resolveDragDisabled(byArchived, archived)).toBe(true);
  });
});

describe('boardDragDisabled', () => {
  it('is true only for a literal board-wide true', () => {
    expect(boardDragDisabled(true)).toBe(true);
    expect(boardDragDisabled(false)).toBe(false);
    expect(boardDragDisabled(undefined)).toBe(false);
  });

  // The regression this whole module exists for. A function is truthy, so the previous
  // `!dragDisabled` overlay test evaluated to false as soon as any per-card policy was
  // supplied — unmounting the DragOverlay for LIVE cards too, which drag fine but would
  // have carried no lifted card. TypeScript cannot catch that; this assertion can.
  it('is FALSE for a per-card predicate, however truthy the value itself is', () => {
    expect(boardDragDisabled(byArchived)).toBe(false);
    // even one that disables every card it is asked about
    expect(boardDragDisabled(() => true)).toBe(false);
  });
});
