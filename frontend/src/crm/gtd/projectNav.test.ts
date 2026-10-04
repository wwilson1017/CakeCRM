// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { isTypingTarget, projectNeighbours } from './projectNav';

const P = (id: number, status: 'active' | 'completed' = 'active') => ({ id, name: `p${id}`, status });
const ids = (n: ReturnType<typeof projectNeighbours>) => n && { prev: n.prev.id, next: n.next.id };

describe('projectNeighbours', () => {
  const list = [P(5), P(2, 'completed'), P(9), P(1), P(7, 'completed')];

  it('follows list order within the same status, never re-sorting', () => {
    expect(ids(projectNeighbours(list, 9))).toEqual({ prev: 5, next: 1 });
    expect(ids(projectNeighbours(list, 2))).toEqual({ prev: 7, next: 7 });
  });

  it('wraps around at both ends', () => {
    expect(ids(projectNeighbours(list, 5))).toEqual({ prev: 1, next: 9 });
    expect(ids(projectNeighbours(list, 1))).toEqual({ prev: 9, next: 5 });
  });

  it('has no neighbours for a single or unknown project', () => {
    expect(projectNeighbours([P(3), P(4, 'completed')], 3)).toBeNull();
    expect(projectNeighbours(list, 42)).toBeNull();
    expect(projectNeighbours([], 1)).toBeNull();
  });
});

describe('isTypingTarget', () => {
  it('is true for inputs, textareas, selects and contenteditable', () => {
    for (const tag of ['input', 'textarea', 'select']) {
      expect(isTypingTarget(document.createElement(tag))).toBe(true);
    }
    const div = document.createElement('div');
    div.contentEditable = 'true';
    // jsdom does not compute isContentEditable from the attribute.
    Object.defineProperty(div, 'isContentEditable', { value: true });
    expect(isTypingTarget(div)).toBe(true);
  });

  it('is false for buttons, links, the body and non-elements', () => {
    expect(isTypingTarget(document.createElement('button'))).toBe(false);
    expect(isTypingTarget(document.createElement('a'))).toBe(false);
    expect(isTypingTarget(document.body)).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
    expect(isTypingTarget(window)).toBe(false);
  });
});
