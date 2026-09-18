// @vitest-environment jsdom
//
// `SearchInput` looks like a text box but is really a two-way state machine, and every one of
// its three behaviours is invisible to `tsc` and to eslint while being individually capable of
// breaking a board:
//
//   • reporting on mount would overwrite a filter state just restored from sessionStorage
//     with a blank query, silently un-filtering the board on every page load;
//   • reporting on every keystroke would re-render a 2,000-row table per character, which is
//     the whole reason the typing state is local;
//   • failing to adopt an external change would leave stale text sitting in the box after a
//     Clear — or, worse, push that stale text back up and undo the Clear.
//
// jsdom is opted into per-file via the docblock, per AGENTS.md; the harness is `createRoot` +
// React 19's `act`, following `the blueprint's debounce-hook test`.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SearchInput from './SearchInput';

// React 19's `act` refuses to run without this flag, and the failure mode is a warning plus
// effects that never flush — i.e. assertions that fail for a harness reason, not a code one.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * Type into a React-controlled input.
 *
 * Assigning `el.value` directly is not enough: React tracks the last value it wrote on the
 * DOM node and skips the change event when the property is set behind its back, so the
 * handler never fires. Going through the prototype's native setter updates the node while
 * leaving React's tracker stale, which is what makes the dispatched event count as a change.
 */
function setInputValue(el: HTMLInputElement, text: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  setter?.call(el, text);
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

function mount(props: { value: string; onChange: (v: string) => void; resetNonce?: number }) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  let root!: Root;
  act(() => {
    root = createRoot(container);
    root.render(<SearchInput {...props} />);
  });
  const input = () => container.querySelector('input') as HTMLInputElement;
  return {
    input,
    // Scoped to this mount's container, never `document` — a stray match from another
    // test's leftover node would silently click the wrong button.
    clearButton: () => container.querySelector('button[aria-label="Clear search"]') as HTMLButtonElement | null,
    type: (text: string) => act(() => setInputValue(input(), text)),
    rerender: (next: typeof props) => act(() => root.render(<SearchInput {...next} />)),
    unmount: () => { act(() => root.unmount()); container.remove(); },
  };
}

afterEach(() => vi.useRealTimers());

describe('SearchInput', () => {
  it('does not report on mount, so a restored filter state survives first render', () => {
    vi.useFakeTimers();
    const onChange = vi.fn();
    const h = mount({ value: 'restored', onChange });
    expect(h.input().value).toBe('restored');
    act(() => { vi.advanceTimersByTime(1000); });
    expect(onChange).not.toHaveBeenCalled();
    h.unmount();
  });

  it('reports once after the debounce, not once per keystroke', () => {
    vi.useFakeTimers();
    const onChange = vi.fn();
    const h = mount({ value: '', onChange });
    h.type('a');
    h.type('ac');
    h.type('acm');
    expect(onChange).not.toHaveBeenCalled();
    act(() => { vi.advanceTimersByTime(250); });
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith('acm');
    h.unmount();
  });

  it('adopts an external value change into the box without reporting it back', () => {
    vi.useFakeTimers();
    const onChange = vi.fn();
    const h = mount({ value: '', onChange });
    h.type('acm');
    act(() => { vi.advanceTimersByTime(250); });
    // Model a real controlled parent: it accepted the reported query.
    h.rerender({ value: 'acm', onChange });
    onChange.mockClear();

    // Now the page clears the filter from outside (a Clear-filters button).
    h.rerender({ value: '', onChange });
    expect(h.input().value).toBe('');
    act(() => { vi.advanceTimersByTime(1000); });
    // The in-flight text must not be pushed back up and undo the clear.
    expect(onChange).not.toHaveBeenCalled();
    h.unmount();
  });

  it('empties the box on an explicit reset even when the settled query was already blank', () => {
    // The Clear-undo bug this exists to prevent: a facet (not the query) is what made "Clear
    // filters" visible, so the page's settled query is ALREADY ''. Clearing does not change
    // `value`, so a plain value comparison sees nothing to adopt — and the text the user just
    // typed survives, then the pending debounce re-applies it a quarter-second later.
    vi.useFakeTimers();
    const onChange = vi.fn();
    const h = mount({ value: '', onChange, resetNonce: 0 });
    h.type('acme');
    expect(h.input().value).toBe('acme');

    h.rerender({ value: '', onChange, resetNonce: 1 });
    expect(h.input().value).toBe('');
    act(() => { vi.advanceTimersByTime(1000); });
    expect(onChange).not.toHaveBeenCalled();
    h.unmount();
  });

  it('clears the box and reports the empty query from its × button', () => {
    vi.useFakeTimers();
    const onChange = vi.fn();
    const h = mount({ value: 'acm', onChange });
    const clear = h.clearButton();
    expect(clear).not.toBeNull();
    act(() => { clear?.click(); });
    act(() => { vi.advanceTimersByTime(250); });
    expect(onChange).toHaveBeenCalledWith('');
    h.unmount();
  });
});
