// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BackupsCard } from './BackupsCard';
import { BackupWarning } from './BackupWarning';

const api = vi.hoisted(() => vi.fn());
const apiResponse = vi.hoisted(() => vi.fn());
vi.mock('../../core/api/client', () => ({ api, apiResponse, attachmentName: () => 'cakecrm-test.dump.age' }));
vi.mock('../../shared/toast', () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
const initial = {
  configured: true, endpoint: 'https://storage.example.test', bucket: 'bucket', region: 'auto',
  prefix: 'own/', access_key_id: 'test-id', has_secret: true, has_passphrase: true,
  retention: 30, last_success_at: null, last_success_bytes: null, last_error: null, stale: false,
};
let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  api.mockReset().mockResolvedValue(initial);
  apiResponse.mockReset();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function click(label: string) {
  const button = Array.from(container.querySelectorAll('button')).find(b => b.textContent === label);
  if (!button) throw new Error(`Missing ${label}`);
  await act(async () => button.click());
}

it('keeps the saved secret out of the form and sends blank to retain it', async () => {
  await act(async () => root.render(<BackupsCard isMobile={false} />));
  const secret = container.querySelector('input[type="password"]') as HTMLInputElement;
  expect(secret.value).toBe('');
  expect(secret.placeholder).toContain('Saved');
  await act(async () => { container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); });
  const [url, opts] = api.mock.calls.at(-1)!;
  expect(url).toBe('/api/backups/destination');
  expect(JSON.parse(opts.body)).toEqual({ endpoint: initial.endpoint, bucket: 'bucket', region: 'auto', prefix: 'own/', access_key_id: 'test-id', secret_key: '', retention: 30 });
});

it('tests saved settings and removes a passphrase with an explicit null', async () => {
  await act(async () => root.render(<BackupsCard isMobile />));
  await click('Test connection');
  expect(api).toHaveBeenCalledWith('/api/backups/test', { method: 'POST' });
  await click('Remove passphrase');
  expect(api).toHaveBeenCalledWith('/api/backups/passphrase', { method: 'PUT', body: '{"passphrase":null}' });
  expect(container.textContent).toContain('If you lose this passphrase');
});

it('downloads the encrypted response with its attachment filename', async () => {
  let name = '';
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { name = this.download; });
  const create = vi.fn(() => 'blob:test');
  vi.stubGlobal('URL', class extends URL { static createObjectURL = create; });
  vi.useFakeTimers();
  apiResponse.mockResolvedValue({ blob: async () => new Blob(['encrypted']), headers: new Headers() });
  await act(async () => root.render(<BackupsCard isMobile={false} />));
  await click('Download backup now');
  expect(apiResponse).toHaveBeenCalledWith('/api/backups/download');
  expect(name).toBe('cakecrm-test.dump.age');
  expect(create).toHaveBeenCalledOnce();
});

it('only fetches and shows a stale banner for admins', async () => {
  api.mockResolvedValue({ stale: true });
  await act(async () => root.render(<MemoryRouter><BackupWarning isAdmin={false} /></MemoryRouter>));
  expect(api).not.toHaveBeenCalled();
  await act(async () => root.render(<MemoryRouter><BackupWarning isAdmin /></MemoryRouter>));
  expect(container.querySelector('[role="alert"]')?.textContent).toContain('two days');
  expect(container.querySelector('a')?.getAttribute('href')).toBe('/crm/settings?section=workspace');
  await act(async () => root.render(<MemoryRouter><BackupWarning isAdmin={false} /></MemoryRouter>));
  expect(container.textContent).toBe('');
});

it('does not show a warning for a healthy destination', async () => {
  await act(async () => root.render(<MemoryRouter><BackupWarning isAdmin /></MemoryRouter>));
  expect(container.querySelector('[role="alert"]')).toBeNull();
});
