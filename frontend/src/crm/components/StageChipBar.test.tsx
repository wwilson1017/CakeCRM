// @vitest-environment jsdom
//
// The chip bar is mobile-only, so the page suite (which pins desktop) never reaches it.
// What matters here is that it never scrolls the DOCUMENT: it used to call scrollIntoView
// on the active chip, which walks every scrollable ancestor — once the bar itself had
// scrolled out of view, swiping between columns yanked the whole page back to it.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import StageChipBar from './StageChipBar';

const STAGES = [
  { stage: 'lead', count: 3 },
  { stage: 'qualified', count: 1 },
  { stage: 'won', count: 0 },
];

let container: HTMLDivElement;
let root: Root;

function render(node: React.ReactNode) {
  act(() => { root.render(node); });
}

const chips = () => [...document.querySelectorAll('button')];

beforeEach(() => {
  // jsdom implements no layout and ships neither method on Element; the component calls
  // scrollTo on its rail, so every test needs it present, not only the ones asserting on it.
  Element.prototype.scrollTo = vi.fn() as unknown as Element['scrollTo'];
  Element.prototype.scrollIntoView = vi.fn();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('rendering', () => {
  it('renders one chip per stage, title-cased, with its count', () => {
    render(<StageChipBar stages={STAGES} activeStage="lead" onSelect={() => {}} />);
    expect(chips().map(c => c.textContent)).toEqual(['Lead3', 'Qualified1', 'Won0']);
  });

  it('marks only the active chip', () => {
    render(<StageChipBar stages={STAGES} activeStage="qualified" onSelect={() => {}} />);
    expect(chips().map(c => c.getAttribute('aria-current'))).toEqual([null, 'true', null]);
  });

  it('renders nothing when the board has no visible columns', () => {
    render(<StageChipBar stages={[]} activeStage={null} onSelect={() => {}} />);
    expect(chips()).toHaveLength(0);
  });
});

describe('selection', () => {
  it('reports the stage that was tapped', () => {
    const onSelect = vi.fn();
    render(<StageChipBar stages={STAGES} activeStage="lead" onSelect={onSelect} />);
    act(() => { chips()[2].click(); });
    expect(onSelect).toHaveBeenCalledWith('won');
  });
});

describe('following the active column never scrolls the page', () => {
  it('adjusts the rail\'s own scrollLeft and calls no scrollIntoView', () => {
    const scrollIntoView = Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>;
    const scrollTo = Element.prototype.scrollTo as unknown as ReturnType<typeof vi.fn>;

    render(<StageChipBar stages={STAGES} activeStage="lead" onSelect={() => {}} />);
    scrollTo.mockClear();
    // Swiping to another column changes the active stage.
    render(<StageChipBar stages={STAGES} activeStage="won" onSelect={() => {}} />);

    expect(scrollIntoView).not.toHaveBeenCalled();
    expect(scrollTo).toHaveBeenCalledTimes(1);
    const arg = scrollTo.mock.calls[0][0] as { left: number; behavior: string };
    expect(arg.behavior).toBe('smooth');
    // Never negative — a chip near the start must not ask for a negative scroll offset.
    expect(arg.left).toBeGreaterThanOrEqual(0);
  });

  it('does nothing when no stage is active', () => {
    const scrollTo = Element.prototype.scrollTo as unknown as ReturnType<typeof vi.fn>;
    render(<StageChipBar stages={STAGES} activeStage={null} onSelect={() => {}} />);
    expect(scrollTo).not.toHaveBeenCalled();
  });
});
