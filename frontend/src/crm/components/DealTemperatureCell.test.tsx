// @vitest-environment jsdom
//
// Issue #125. The List view reaches its temperature writer through a context rather than an
// argument, because its columns are built in a `useMemo` that may not hold a ref-reading
// callback. That indirection has exactly two ways to fail silently, and both are here:
//
//  - the provider is wired but the cell does not use it, so every List row renders a control
//    that looks live and does nothing;
//  - the provider is absent and the cell renders an interactive control anyway, so a surface
//    with no writer advertises editing it cannot do.
//
// Neither shows up in a type check or a screenshot.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DealTemperatureCell, DealTemperatureWriter } from './DealTemperatureCell';
import { buildPipelineListColumns } from './pipelineListColumns';
import type { CrmDeal } from '../../core/types';

function deal(over: Partial<CrmDeal> = {}): CrmDeal {
  return {
    id: 7, contact_id: null, company_id: null, title: 'Wedding cake order', stage: 'qualified',
    value: 1200, notes: '', expected_close_date: '', probability: 40, currency: 'USD',
    created_at: '', updated_at: '', deal_temperature: 'warm', ...over,
  };
}

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

const render = (node: React.ReactNode) => act(() => root.render(node));
const button = () => container.querySelector('button');

describe('DealTemperatureCell', () => {
  it('cycles through the provided writer, naming the deal it belongs to', () => {
    const cycle = vi.fn();
    render(
      <DealTemperatureWriter value={cycle}>
        <DealTemperatureCell deal={deal()} />
      </DealTemperatureWriter>,
    );
    act(() => { button()!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    // Warm's next step is Cold, and the cell must pass the row it rendered — a List has many.
    expect(cycle).toHaveBeenCalledWith(expect.objectContaining({ id: 7 }), 'cold');
  });

  it('renders read-only with no provider, rather than a control that cannot write', () => {
    render(<DealTemperatureCell deal={deal()} />);
    expect(button()).toBeNull();
    expect(container.querySelector('[role="img"]')).not.toBeNull();
  });

  it('is inert on an archived deal even with a writer present', () => {
    const cycle = vi.fn();
    render(
      <DealTemperatureWriter value={cycle}>
        <DealTemperatureCell deal={deal()} disabled />
      </DealTemperatureWriter>,
    );
    expect(button()!.disabled).toBe(true);
    act(() => { button()!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    expect(cycle).not.toHaveBeenCalled();
  });
});

describe('the pipeline List column is wired to that cell', () => {
  it('renders the interactive control for a live row when a writer is provided', () => {
    // Guards the seam the context exists to cross: the column builder takes no callback, so
    // nothing but this proves a List row can actually write.
    const column = buildPipelineListColumns(() => 'Someone').find(c => c.key === 'temperature')!;
    const cycle = vi.fn();
    render(
      <DealTemperatureWriter value={cycle}>{column.render(deal())}</DealTemperatureWriter>,
    );
    act(() => { button()!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    expect(cycle).toHaveBeenCalledWith(expect.objectContaining({ id: 7 }), 'cold');
  });

  it('renders an archived row inert, matching the board card', () => {
    const column = buildPipelineListColumns(() => 'Someone').find(c => c.key === 'temperature')!;
    render(
      <DealTemperatureWriter value={vi.fn()}>
        {column.render(deal({ archived_at: '2026-09-01T00:00:00Z' }))}
      </DealTemperatureWriter>,
    );
    expect(button()!.disabled).toBe(true);
  });
});
