// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { CrmCompany } from '../core/types';
import { CompanyDetailPage } from './CompanyDetailPage';

const { api, onChanged, onWriteUncertain } = vi.hoisted(() => ({
  api: vi.fn(), onChanged: vi.fn(), onWriteUncertain: vi.fn(),
}));
vi.mock('../core/api/client', async importOriginal => ({
  ...await importOriginal<typeof import('../core/api/client')>(), api,
}));
vi.mock('./RecordContext', () => ({ usePublishActiveRecord: () => {} }));
vi.mock('../shared/useIsMobile', () => ({ useIsMobile: () => false }));
vi.mock('./components/ActivityTimeline', () => ({ ActivityTimeline: () => null }));
vi.mock('./components/CustomFieldsSection', () => ({ CustomFieldsSection: () => null }));
vi.mock('./components/NotesThread', () => ({ NotesThread: () => null }));
vi.mock('./components/OwnerName', () => ({ OwnerName: () => null }));
vi.mock('./components/CompanyMergeDialog', () => ({
  CompanyMergeDialog: ({ onMerged, onCancel, onWriteUncertain: uncertain }: {
    onMerged: (id: number) => void;
    onCancel: () => void;
    onWriteUncertain: () => Promise<void>;
  }) => <div role="dialog">
    <button onClick={() => onMerged(1)}>Finish merge</button>
    <button onClick={async () => { await uncertain(); onCancel(); }}>Uncertain merge</button>
  </div>,
}));

const duplicate: CrmCompany = { id: 2, name: 'Duplicate', status: 'active', domain: '',
  industry: '', phone: '', address: '', notes: '', source: '', created_at: '', updated_at: '' };
const kept: CrmCompany = { ...duplicate, id: 1, name: 'Kept' };
let current: CrmCompany;
let root: Root;
let container: HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.clearAllMocks();
  current = duplicate;
  api.mockImplementation(async (url: string) => url.endsWith('/2') ? current : kept);
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});
async function mount() {
  await act(async () => root.render(<MemoryRouter initialEntries={['/crm/companies/2']}>
    <Routes><Route path="/crm/companies/:id" element={
      <CompanyDetailPage onChanged={onChanged} onWriteUncertain={onWriteUncertain} />
    } /></Routes>
  </MemoryRouter>));
}
function button(text: string) {
  return [...container.querySelectorAll('button')].find(b => b.textContent === text);
}

it('offers merging without AI and patches both companies before showing the survivor', async () => {
  await mount();
  await act(async () => button('Merge into…')!.click());
  expect(container.querySelector('[role="dialog"]')).not.toBeNull();
  current = { ...duplicate, status: 'archived', merged_into_id: 1 };
  await act(async () => button('Finish merge')!.click());
  expect(onChanged).toHaveBeenCalledWith(current);
  expect(onChanged).toHaveBeenLastCalledWith(kept);
  expect(container.querySelector('h1')?.textContent).toBe('Kept');
  expect(container.querySelector('[role="dialog"]')).toBeNull();
});

it('hides merging for absorbed rows and links to the direct successor', async () => {
  current = { ...duplicate, status: 'archived', merged_into_id: 1 };
  await mount();
  expect(button('Merge into…')).toBeUndefined();
  await act(async () => button('company #1')!.click());
  expect(container.querySelector('h1')?.textContent).toBe('Kept');
});

it('reports uncertain writes, reloads the absorbed row and closes the dialog', async () => {
  await mount();
  await act(async () => button('Merge into…')!.click());
  current = { ...duplicate, status: 'archived', merged_into_id: 1 };
  await act(async () => button('Uncertain merge')!.click());
  expect(onWriteUncertain).toHaveBeenCalledOnce();
  expect(onChanged).toHaveBeenLastCalledWith(current);
  expect(container.querySelector('[role="dialog"]')).toBeNull();
  expect(container.textContent).toContain('Merged into');
});
