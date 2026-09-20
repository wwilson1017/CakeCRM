// @vitest-environment jsdom
//
// Protected context files are admin-only to WRITE (#194, Decision 1d).
//
// The server is the authorization boundary — `put_context_file` returns 403 whatever the
// UI does — so what this pins is the UI's half of the contract: a member can still READ
// soul.md (visibility is the whole point of the Memory page), but the editor does not
// invite them to type a paragraph the server will refuse. An admin is unaffected.
//
// Mocks are the page's two dependencies that need a DOM or a network: the memory API
// module and AuthContext. `useIsMobile` is module-mocked because jsdom has no matchMedia
// (the SettingsPage suite's convention).
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ContextFile, ContextFileMeta } from './memory/api';

const auth = vi.hoisted(() => ({ isAdmin: false }));
vi.mock('../core/auth/AuthContext', () => ({
  useAuth: () => ({
    isAdmin: auth.isAdmin,
    isLoggedIn: true,
    loading: false,
    currentUser: null,
    login: vi.fn(),
    verify2fa: vi.fn(),
    applyToken: vi.fn(),
    logout: vi.fn(),
  }),
}));

vi.mock('../shared/useIsMobile', () => ({ useIsMobile: () => false }));

const SOUL_META: ContextFileMeta = {
  id: 1, filename: 'soul.md', kind: 'soul', headline: 'Who I am',
  is_protected: true, written_by: 'assistant', size_chars: 12,
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-02T00:00:00Z',
};
const TOPIC_META: ContextFileMeta = {
  ...SOUL_META, id: 2, filename: 'topics/pricing.md', kind: 'topic',
  is_protected: false, headline: 'Pricing',
};

const asFile = (meta: ContextFileMeta): ContextFile => ({
  ...meta, content: 'the body', archived_at: null,
});

const saveContextFile = vi.hoisted(() => vi.fn());
const getContextFile = vi.hoisted(() => vi.fn());
vi.mock('./memory/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./memory/api')>()),
  listContextFiles: vi.fn(async () => [SOUL_META, TOPIC_META]),
  getContextFile,
  saveContextFile,
  deleteContextFile: vi.fn(async () => undefined),
  listFacts: vi.fn(async () => []),
  deleteFact: vi.fn(async () => undefined),
}));

let container: HTMLDivElement;
let root: Root;

async function renderPage() {
  const { MemoryPage } = await import('./MemoryPage');
  await act(async () => {
    root.render(<MemoryPage />);
  });
}

/** Click the sidebar entry for a file and wait for its body to load. */
async function openFile(filename: string) {
  const entry = [...container.querySelectorAll('button, div[role="button"], li, a')]
    .find(el => el.textContent?.includes(filename.replace(/^topics\//, '').replace(/\.md$/, '')));
  expect(entry, `no sidebar entry for ${filename}`).toBeTruthy();
  await act(async () => {
    entry!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
}

const textarea = () => container.querySelector('textarea');
const saveButton = () =>
  [...container.querySelectorAll('button')].find(b => /^Sav(e|ing)/.test(b.textContent ?? ''));

beforeEach(() => {
  auth.isAdmin = false;
  saveContextFile.mockReset();
  getContextFile.mockReset();
  getContextFile.mockImplementation(async (name: string) =>
    asFile(name === 'soul.md' ? SOUL_META : TOPIC_META));
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.resetModules();
});

describe('protected context files in the Memory editor', () => {
  it('a member can read soul.md but cannot edit or save it', async () => {
    await renderPage();
    await openFile('soul.md');

    const ta = textarea();
    expect(ta, 'the file body must still be visible to a member').toBeTruthy();
    expect(ta!.value).toBe('the body');
    expect(ta!.readOnly).toBe(true);
    expect(saveButton()).toBeUndefined();
    expect(container.textContent).toContain('An admin maintains this file');
  });

  it('a member can still edit an unprotected topic file', async () => {
    await renderPage();
    await openFile('topics/pricing.md');

    // The gate is selective, not a blanket lock on the page — which is exactly why the
    // backend check is in the handler rather than a route-level require_admin.
    expect(textarea()!.readOnly).toBe(false);
    expect(saveButton()).toBeTruthy();
  });

  it('an admin edits soul.md as before', async () => {
    auth.isAdmin = true;
    await renderPage();
    await openFile('soul.md');

    expect(textarea()!.readOnly).toBe(false);
    expect(saveButton()).toBeTruthy();
    expect(container.textContent).not.toContain('An admin maintains this file');
  });
});
