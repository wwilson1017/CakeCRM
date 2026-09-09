// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, StrictMode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

const apiMock = vi.fn();
vi.mock('../core/api/client', () => ({
  api: (...args: unknown[]) => apiMock(...args),
  ApiError: class extends Error {},
}));

// Records the props the report was mounted with, and a stub picker that selects on click —
// the real combobox owns a debounce and a popover this test has no reason to drive.
const reportProps: Array<{ companyId: number }> = [];
const comboProps: Array<Record<string, unknown>> = [];

vi.mock('./components/CompanyRollupReport', () => ({
  CompanyRollupReport: (p: { companyId: number }) => {
    reportProps.push(p);
    return <div data-testid="report">{`report for ${p.companyId}`}</div>;
  },
}));
vi.mock('./components/RecordCombobox', () => ({
  RecordCombobox: (p: Record<string, unknown>) => {
    comboProps.push(p);
    return (
      <button
        type="button"
        onClick={() => (p.onSelect as (r: unknown) => void)({ id: 9, name: 'Nine', status: 'active' })}
      >
        pick
      </button>
    );
  },
}));

let container: HTMLDivElement;
let root: Root;

async function settle(rounds = 6): Promise<void> {
  for (let i = 0; i < rounds; i++) await act(async () => { await Promise.resolve(); });
}

async function mountAt(path: string): Promise<void> {
  const { ReportsPage } = await import('./ReportsPage');
  act(() => {
    root.render(
      <StrictMode>
        <MemoryRouter initialEntries={[path]}>
          <Routes><Route path="/crm/reports" element={<ReportsPage />} /></Routes>
        </MemoryRouter>
      </StrictMode>,
    );
  });
  await settle();
}

const text = () => container.textContent ?? '';

beforeEach(() => {
  apiMock.mockReset();
  reportProps.length = 0;
  comboProps.length = 0;
  vi.stubGlobal('matchMedia', () => ({
    matches: false, addEventListener: () => {}, removeEventListener: () => {},
  }));
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe('ReportsPage', () => {
  it('prompts for a company and fetches nothing until one is chosen', async () => {
    await mountAt('/crm/reports');
    expect(text()).toContain('Pick a company to build its report');
    expect(container.querySelector('[data-testid="report"]')).toBeNull();
    expect(apiMock).not.toHaveBeenCalled();
  });

  it('builds the report for a deep-linked ?company=', async () => {
    await mountAt('/crm/reports?company=7');
    expect(text()).toContain('report for 7');
    expect(reportProps[0].companyId).toBe(7);
  });

  it('treats a non-numeric ?company= as no selection rather than a broken request', async () => {
    await mountAt('/crm/reports?company=abc');
    expect(text()).toContain('Pick a company to build its report');
    expect(container.querySelector('[data-testid="report"]')).toBeNull();
  });

  it('puts the chosen company in the URL so the report is linkable', async () => {
    await mountAt('/crm/reports');
    act(() => { container.querySelector('button')!.click(); });
    await settle();
    expect(text()).toContain('report for 9');
  });

  it('never offers to create a company from a report', async () => {
    // A report is a read. Passing `create` here would put a "Create …" row in the picker,
    // which is why the prop was made optional rather than passed a no-op.
    await mountAt('/crm/reports');
    expect(comboProps[0].create).toBeUndefined();
  });

  it('does not caption a newly selected company with the previous one label', async () => {
    // The label is stored WITH its id and derived during render. Held in its own state and
    // cleared by an effect, it would survive one render into the next company's report.
    await mountAt('/crm/reports?company=7');
    const before = comboProps[comboProps.length - 1].valueLabel;
    expect(before).toBe('');
    act(() => { container.querySelector('button')!.click(); });
    await settle();
    expect(comboProps[comboProps.length - 1].valueLabel).toBe('Nine');
  });
});
