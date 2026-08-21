// @vitest-environment jsdom
//
// WHY THIS FILE EXISTS. Consumers legitimately read `onChange` as "the view
// switched" and reset view-scoped state from it — CRM clears its bulk selection
// there, because a selection carried into the list is unclearable and would
// leave a live "Bulk Update (n)". That makes a spurious `onChange` on a no-op
// click destructive rather than merely noisy, which is exactly the defect the
// Codex reviewer caught on an earlier review. Pinned so it can't come back.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ViewSwitcher from './ViewSwitcher';

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

const button = (el: HTMLElement, label: string) =>
  Array.from(el.querySelectorAll('button')).find(b => b.textContent === label)!;

describe('ViewSwitcher', () => {
  it('does NOT fire onChange when the already-active segment is clicked', () => {
    const onChange = vi.fn();
    const el = render(<ViewSwitcher view="kanban" onChange={onChange} />);
    act(() => { button(el, 'Board').click(); });
    expect(onChange).not.toHaveBeenCalled();
  });

  it('fires onChange with the new value on a real switch', () => {
    const onChange = vi.fn();
    const el = render(<ViewSwitcher view="kanban" onChange={onChange} />);
    act(() => { button(el, 'List').click(); });
    expect(onChange).toHaveBeenCalledWith('list');
  });

  it('marks the active segment with aria-pressed', () => {
    const el = render(<ViewSwitcher view="list" onChange={() => {}} />);
    expect(button(el, 'List').getAttribute('aria-pressed')).toBe('true');
    expect(button(el, 'Board').getAttribute('aria-pressed')).toBe('false');
  });
});
