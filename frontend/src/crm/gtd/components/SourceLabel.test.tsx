// @vitest-environment jsdom
//
// #260: a todo a person did not type into the app says who added it, and the row's
// accessible name carries that — asserted on the NAME (textContent minus aria-hidden
// subtrees, the #162 convention), since the label is visually just one word.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { todoSourceLabel } from '../sourceLabel';
import type { Todo } from '../types';
import { TodoRow } from './TodoRow';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function todo(source: string): Todo {
  return {
    id: 1, title: 'Call the supplier', notes: '', project_id: null, project_name: null,
    context: '', tags: [], status: 'inbox', star: false, due_date: '', repeat: '',
    auto_star_on_due: false, source, created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z', completed_at: null, contact_id: null, deal_id: null,
  };
}

function accessibleText(el: Element): string {
  const clone = el.cloneNode(true) as Element;
  clone.querySelectorAll('[aria-hidden="true"]').forEach(n => n.remove());
  return (clone.textContent ?? '').replace(/\s+/g, ' ').trim();
}

describe('todoSourceLabel', () => {
  it('names every non-human source and nothing else', () => {
    expect(todoSourceLabel('agent')?.text).toBe('Baker');
    expect(todoSourceLabel('observer')?.text).toBe('Observer');
    expect(todoSourceLabel('capture_web')?.text).toBe('Capture link');
    expect(todoSourceLabel('telegram')?.text).toBe('Telegram');
    expect(todoSourceLabel('ui')).toBeNull();
    // An unknown value claims nothing, and an inherited key is not a source.
    expect(todoSourceLabel('web')).toBeNull();
    expect(todoSourceLabel('toString')).toBeNull();
  });
});

describe('TodoRow added-by label', () => {
  let host: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
  });
  afterEach(() => {
    act(() => root.unmount());
    host.remove();
  });

  function rowButtonName(source: string): string {
    act(() => {
      root.render(
        <TodoRow todo={todo(source)} onToggleDone={() => {}} onToggleStar={() => {}} onEdit={() => {}} />,
      );
    });
    const button = host.querySelector('button')!;
    return accessibleText(button);
  }

  it.each([
    ['agent', 'Added by Baker'],
    ['observer', 'Added by Observer'],
    ['capture_web', 'Added by Capture link'],
    ['telegram', 'Added by Telegram'],
  ])('labels a %s todo in the row button\'s accessible name', (source, expected) => {
    // textContent joins the title <p> and the meta <p> with no space; a screen reader
    // separates the blocks, so the pin is the phrase, not the join.
    expect(rowButtonName(source)).toContain(expected);
    expect(accessibleText(host.querySelector(`[data-todo-source="${source}"]`)!)).toBe(expected);
  });

  it('shows nothing on a todo a person added', () => {
    expect(rowButtonName('ui')).toBe('Call the supplier');
    expect(host.querySelector('[data-todo-source]')).toBeNull();
  });
});
