// @vitest-environment jsdom
//
// The Mark Won "Closed on" dialog (#279): defaults to today, accepts a past day, refuses a
// future one (typing bypasses `max`), confirms exactly once, cancels on Escape, and renders
// through a portal like the Mark Lost dialog it was cloned from.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ymd } from '../pipelineFilters';
import { ClosedOnModal } from './ClosedOnModal';

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(onConfirm = vi.fn(), onCancel = vi.fn()) {
  act(() => {
    root.render(<ClosedOnModal dealTitle="Wholesale order" onConfirm={onConfirm} onCancel={onCancel} />);
  });
  return { onConfirm, onCancel };
}

// The portal target is document.body; querying `container` would find nothing.
const field = () => document.body.querySelector<HTMLInputElement>('#crm-closed-on-input')!;
const button = (label: string) =>
  [...document.body.querySelectorAll('button')]
    .find(b => b.textContent?.trim() === label) as HTMLButtonElement;

function type(value: string) {
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field(), value);
    field().dispatchEvent(new Event('input', { bubbles: true }));
  });
}

const today = () => ymd(new Date());
const tomorrow = () => ymd(new Date(), 1);

describe('ClosedOnModal', () => {
  it('defaults to today and caps the picker at today', () => {
    render();
    expect(field().value).toBe(today());
    expect(field().max).toBe(today());
    expect(button('Confirm').disabled).toBe(false);
  });

  it('confirms the default day once, even on a double click', () => {
    const { onConfirm } = render();
    act(() => { button('Confirm').click(); button('Confirm').click(); });
    expect(onConfirm.mock.calls).toEqual([[today()]]);
  });

  it('accepts a backdated day', () => {
    const { onConfirm } = render();
    type('2025-12-31');
    act(() => { button('Confirm').click(); });
    expect(onConfirm).toHaveBeenCalledWith('2025-12-31');
  });

  it('refuses a typed future day: Confirm disabled, an alert, and Enter does nothing', () => {
    const { onConfirm } = render();
    type(tomorrow());
    expect(button('Confirm').disabled).toBe(true);
    expect(document.body.querySelector('[role="alert"]')?.textContent).toContain('future');
    act(() => {
      field().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('refuses a cleared field', () => {
    const { onConfirm } = render();
    type('');
    expect(button('Confirm').disabled).toBe(true);
    act(() => { button('Confirm').click(); });
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('Escape cancels without confirming', () => {
    const { onConfirm, onCancel } = render();
    act(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('renders through a portal, outside the host container', () => {
    render();
    const dialog = document.body.querySelector('[role="dialog"]')!;
    expect(dialog).toBeTruthy();
    expect(container.contains(dialog)).toBe(false);
  });
});
