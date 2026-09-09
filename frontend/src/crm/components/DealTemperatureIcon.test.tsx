// @vitest-environment jsdom
//
// Issue #125. The control is small, but three of its properties are the kind that break
// silently and are invisible in a screenshot:
//
//  1. It lives inside a card and a table row that BOTH open the deal sheet on click. If the
//     click bubbles, every attempt to mark a deal hot opens the sheet instead — and the
//     temperature still changes, so it reads as "the app is doing two things at once".
//  2. Its four states must not be distinguishable by colour alone (WCAG 1.4.1). That is a
//     property of the rendered shapes, which a person eyeballing light mode cannot check.
//  3. Without `onCycle` it must render no button at all. A focus stop that does nothing is
//     worse than no focus stop — the rule `shared/collection` already follows for rows.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import DealTemperatureIcon, { dealTemperatureRenderKind } from './DealTemperatureIcon';
import { DEAL_TEMPERATURES, nextTemperature, type DealTemperature } from '../dealTemperature';

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

function render(node: React.ReactNode) {
  act(() => root.render(node));
}

function button(): HTMLButtonElement | null {
  return container.querySelector('button');
}

describe('DealTemperatureIcon', () => {
  it('cycles to the next tier on click, for every starting state', () => {
    for (const start of [null, 'hot', 'warm', 'cold'] as const) {
      const onCycle = vi.fn();
      render(<DealTemperatureIcon value={start} onCycle={onCycle} />);
      act(() => { button()!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
      expect(onCycle).toHaveBeenCalledWith(nextTemperature(start));
    }
  });

  it('does not let the click reach the card or row that hosts it', () => {
    // The host opens the deal sheet on any click inside it. Both `PipelinePage`'s board card
    // and `CollectionListView`'s <tr> work this way, so a bubbling click means marking a deal
    // hot also opens it.
    const hostClick = vi.fn();
    const onCycle = vi.fn();
    render(
      <div onClick={hostClick}>
        <DealTemperatureIcon value="warm" onCycle={onCycle} />
      </div>,
    );
    act(() => { button()!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    expect(onCycle).toHaveBeenCalledTimes(1);
    expect(hostClick).not.toHaveBeenCalled();
  });

  it('does not let a keystroke reach the host either, and does not double-fire', () => {
    // A <button> already activates on Enter/Space. Handling the key here as well would run
    // the cycle twice per press; not stopping it would also open the deal.
    const hostKey = vi.fn();
    const onCycle = vi.fn();
    render(
      <div onKeyDown={hostKey}>
        <DealTemperatureIcon value={null} onCycle={onCycle} />
      </div>,
    );
    act(() => {
      button()!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });
    expect(hostKey).not.toHaveBeenCalled();
    expect(onCycle).not.toHaveBeenCalled(); // the keydown alone is not an activation
  });

  it('renders no button and no tab stop when it has no writer', () => {
    render(<DealTemperatureIcon value="hot" />);
    expect(button()).toBeNull();
    expect(container.querySelector('[role="img"]')).not.toBeNull();
    expect(container.querySelector('[tabindex]')).toBeNull();
  });

  it('names the current tier and the one a click would set', () => {
    render(<DealTemperatureIcon value="warm" onCycle={vi.fn()} />);
    expect(button()!.getAttribute('aria-label')).toBe('Deal temperature: Warm. Click to set Cold.');

    render(<DealTemperatureIcon value={null} onCycle={vi.fn()} />);
    expect(button()!.getAttribute('aria-label')).toBe('Deal temperature: not set. Click to set Hot.');
  });

  it('says "not set", never "Cold", for an untriaged deal', () => {
    // The distinction the scoring factor rests on. Labelling an untriaged deal "Cold" would
    // tell the rep the CRM holds a judgment it does not hold.
    render(<DealTemperatureIcon value={null} />);
    expect(container.querySelector('[role="img"]')!.getAttribute('aria-label'))
      .toBe('Deal temperature: not set');
  });

  it('is inert while disabled — an archived deal is found and restored, not worked', () => {
    const onCycle = vi.fn();
    render(<DealTemperatureIcon value="hot" onCycle={onCycle} disabled />);
    expect(button()!.disabled).toBe(true);
    act(() => { button()!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    expect(onCycle).not.toHaveBeenCalled();
  });

  it('advances on consecutive clicks without waiting for the prop to catch up', async () => {
    // THE test for the optimistic override. Without it every click after the first steps from
    // the same unchanged prop, so three clicks produce one move and the control reads as
    // broken — which is the whole reason a click-to-cycle affordance needs local state.
    let settle: () => void = () => {};
    const onCycle = vi.fn(() => new Promise<void>(res => { settle = res; }));
    render(<DealTemperatureIcon value={null} onCycle={onCycle} />);

    for (const expected of ['hot', 'warm', 'cold', null]) {
      await act(async () => { button()!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
      expect(onCycle).toHaveBeenLastCalledWith(expected);
    }
    // The prop never moved — every step came from what the control was showing.
    expect(onCycle).toHaveBeenCalledTimes(4);
    settle();
  });

  it('keeps the optimistic glyph until its own write settles, then follows the prop', async () => {
    let settle: () => void = () => {};
    const onCycle = vi.fn(() => new Promise<void>(res => { settle = res; }));
    render(<DealTemperatureIcon value={null} onCycle={onCycle} />);

    await act(async () => { button()!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    expect(button()!.getAttribute('aria-label')).toContain('Deal temperature: Hot');

    // Settling with the prop STILL stale is the failure case worth pinning: the host has not
    // patched yet, so releasing here shows the pre-click value again.
    await act(async () => { settle(); });
    expect(button()!.getAttribute('aria-label')).toContain('Deal temperature: not set');

    // ...and once the host does patch, the prop is what shows.
    render(<DealTemperatureIcon value="hot" onCycle={onCycle} />);
    expect(button()!.getAttribute('aria-label')).toContain('Deal temperature: Hot');
  });

  it('a superseded write settling does not flash the glyph back past a newer click', async () => {
    // #150's lesson, in miniature: an EARLIER write of this control's own, answering after a
    // later one, is exactly the settle that must not clear the newer override.
    const settlers: (() => void)[] = [];
    const onCycle = vi.fn(() => new Promise<void>(res => { settlers.push(res); }));
    render(<DealTemperatureIcon value={null} onCycle={onCycle} />);

    await act(async () => { button()!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    await act(async () => { button()!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    expect(button()!.getAttribute('aria-label')).toContain('Deal temperature: Warm');

    await act(async () => { settlers[0](); });   // the FIRST write lands second
    expect(button()!.getAttribute('aria-label')).toContain('Deal temperature: Warm');
  });

  it('puts the glyph back when its own write fails', async () => {
    // The revert is silent by design — the caller owns the message, because only it knows
    // whether the failure is worth a toast or is a bulk-lock the board already explains.
    const onCycle = vi.fn(() => Promise.reject(new Error('nope')));
    render(<DealTemperatureIcon value="warm" onCycle={onCycle} />);
    await act(async () => { button()!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    expect(button()!.getAttribute('aria-label')).toContain('Deal temperature: Warm');
  });

  it('distinguishes every adjacent pair of states by something other than colour', () => {
    // WCAG 1.4.1: colour must not be the only visual means of conveying information. Walking
    // the cycle is the right traversal — these are the pairs a reader actually has to tell
    // apart, and it is exactly the comparison that gets lost when a hue is retuned later.
    const cycleOrder: (DealTemperature | null)[] = [null, 'hot', 'warm', 'cold'];
    expect(cycleOrder.slice(1).sort()).toEqual([...DEAL_TEMPERATURES].sort());

    const adjacentPairs = cycleOrder.map((tier, i) => [tier, cycleOrder[(i + 1) % cycleOrder.length]] as const);
    for (const [a, b] of adjacentPairs) {
      expect(
        dealTemperatureRenderKind(a),
        `${a ?? 'not set'} and ${b ?? 'not set'} are adjacent in the cycle and must not be told apart by hue alone`,
      ).not.toBe(dealTemperatureRenderKind(b));
    }
  });

  it('draws the kind it declares, so the mapping above is not a comment on its own', () => {
    expect(dealTemperatureRenderKind('hot')).toBe('flame');
    expect(dealTemperatureRenderKind('warm')).toBe('filled-dot');
    expect(dealTemperatureRenderKind('cold')).toBe('ring');
    expect(dealTemperatureRenderKind(null)).toBe('dashed-ring');

    // The flame is the only SVG — the tier #131 will read is the one that stands out.
    render(<DealTemperatureIcon value="hot" onCycle={vi.fn()} />);
    expect(container.querySelector('svg')).not.toBeNull();
    expect(container.querySelector('[data-temperature-glyph]')).toBeNull();

    for (const [tier, kind] of [['warm', 'filled-dot'], ['cold', 'ring'], [null, 'dashed-ring']] as const) {
      render(<DealTemperatureIcon value={tier} onCycle={vi.fn()} />);
      expect(container.querySelector('svg')).toBeNull();
      expect(container.querySelector('[data-temperature-glyph]')!.getAttribute('data-temperature-glyph')).toBe(kind);
    }
  });
});
