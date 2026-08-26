// @vitest-environment jsdom
//
// What this pins is the Settings SHELL's contract, not any card's internals (#103):
//
//   • the gating partition survives the restructure — a member reaches exactly four cards
//     across two sections, an admin exactly nine across four, and walking the nav is the
//     only way to prove it, because the page renders one section at a time;
//   • the nav is real navigation — anchors with hrefs, one `aria-current="page"`, and no
//     `role="tab"` promising a keyboard model we do not implement;
//   • the Gmail OAuth callback still works. `/crm/settings?gmail=connected` must select
//     Integrations (or GmailCard never mounts, never toasts, and never clears the param),
//     and after it strips those params the view must STAY on Integrations. That round-trip
//     is the one behavioural regression a section-based layout can introduce, and it is
//     invisible to every other check;
//   • an `isAdmin` flip is reflected on the next render in BOTH directions — nothing here
//     freezes the role.
//
// Two firsts for this repo's harness: no other test renders under a Router, and none
// renders a `useIsMobile` consumer (jsdom has no `matchMedia`, so the hook is module-mocked
// rather than shimmed).
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getToasts, _resetForTesting as resetToasts } from '../shared/toast';
import { invalidateUsers } from './useUsers';

// ── Mocks ────────────────────────────────────────────────────────────────────
// Relative to THIS file; the cards' own `../../core/...` imports resolve to the same
// modules, so one mock each covers the whole tree.

const api = vi.hoisted(() => vi.fn());
vi.mock('../core/api/client', () => ({ api }));

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

const branding = vi.hoisted(() => ({ company_name: 'CakeCRM', has_logo: false }));
vi.mock('../core/branding/BrandingContext', () => ({
  useBranding: () => ({
    branding,
    loadError: false,
    patchBranding: vi.fn(),
    logoVersion: 1,
    bumpLogoVersion: vi.fn(),
  }),
}));

const mobile = vi.hoisted(() => ({ value: false }));
vi.mock('../shared/useIsMobile', () => ({ useIsMobile: () => mobile.value }));

const { SettingsPage } = await import('./SettingsPage');

// Every endpoint the nine cards hit on mount. Anything unlisted resolves to `{}` rather
// than rejecting, so an unmocked card renders its empty state instead of exploding.
const RESPONSES: Record<string, unknown> = {
  '/api/users': { users: [] },
  '/api/crm/fields': [],
  '/api/telegram/status': { connected: false, bot_username: '', linked: false, linked_name: '', link_code: '', link_url: '' },
  '/api/setup/status': { ai_ready: true },
  '/api/heartbeat/status': { state: { proactive_enabled: true }, proactive_digest_hour: 8 },
  '/api/gmail/status': {
    connected: false, email: '', connection_status: '', client_id: '',
    client_secret_present: false, scopes: [], redirect_uri: 'https://example.test/api/gmail/oauth/callback',
  },
  '/api/auth/2fa/status': { enabled: false },
  '/api/crm/demo-status': { task_mode: 'normal' },
  '/api/crm/todo-surfaces': null,
};

const ADMIN_ONLY_TITLES = ['Branding', 'Team', 'Custom Fields', 'Telegram', 'Gmail'];
const MEMBER_TITLES = ['Notifications', 'Change password', 'Assistant memory', 'Task mode'];

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  api.mockReset();
  // Reinstall AFTER the reset — mockReset() clears the implementation, and a bare mock
  // resolves `undefined`, which would make every card render as if its fetch failed.
  api.mockImplementation((path: string) =>
    Promise.resolve(RESPONSES[path.split('?')[0]] ?? {}));
  auth.isAdmin = false;
  mobile.value = false;
  resetToasts();
  invalidateUsers(); // the users cache is module-level and outlives a test
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render(url = '/crm/settings') {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[url]}>
        <SettingsPage />
        <LocationProbe />
      </MemoryRouter>,
    );
  });
}

/** Exposes the live query string so a test can see what GmailCard rewrote it to. */
function LocationProbe() {
  return <span data-testid="location" data-search={useLocation().search} />;
}

const searchNow = () =>
  container.querySelector('[data-testid="location"]')!.getAttribute('data-search') ?? '';

const navLinks = () =>
  Array.from(container.querySelectorAll<HTMLAnchorElement>('nav[aria-label="Settings sections"] a'));

const navLabels = () => navLinks().map(a => a.textContent);

const cardTitles = () =>
  Array.from(container.querySelectorAll('section > h2')).map(h => h.textContent?.trim() ?? '');

async function clickTab(label: string) {
  const link = navLinks().find(a => a.textContent === label);
  if (!link) throw new Error(`No "${label}" tab. Have: ${navLabels().join(', ')}`);
  await act(async () => { link.click(); });
}

/** Walk every tab and collect every card title reachable in this role. */
async function allReachableTitles(): Promise<string[]> {
  const seen: string[] = [];
  for (const label of navLabels()) {
    await clickTab(label!);
    seen.push(...cardTitles());
  }
  return seen;
}

describe('SettingsPage — the gating partition survives the restructure', () => {
  it('reaches a member exactly the four member-visible cards, across two sections', async () => {
    await render();
    expect(navLabels()).toEqual(['Personal', 'Assistant']);

    const reachable = await allReachableTitles();
    expect(reachable.sort()).toEqual([...MEMBER_TITLES].sort());
    for (const adminOnly of ADMIN_ONLY_TITLES) {
      expect(reachable).not.toContain(adminOnly);
    }
  });

  it('reaches an admin all nine cards, across four sections', async () => {
    auth.isAdmin = true;
    await render();
    expect(navLabels()).toEqual(['Personal', 'Assistant', 'Workspace', 'Integrations']);

    const reachable = await allReachableTitles();
    expect(reachable).toHaveLength(9);
    expect(reachable.sort()).toEqual([...MEMBER_TITLES, ...ADMIN_ONLY_TITLES].sort());
  });

  it('sends a member deep-linked to an admin section back to Personal', async () => {
    await render('/crm/settings?section=workspace');
    expect(navLabels()).not.toContain('Workspace');
    expect(container.querySelector('[aria-current="page"]')!.textContent).toBe('Personal');
    expect(cardTitles()).toEqual(['Notifications', 'Change password']);
  });

  it('honours an admin deep link', async () => {
    auth.isAdmin = true;
    await render('/crm/settings?section=workspace');
    expect(container.querySelector('[aria-current="page"]')!.textContent).toBe('Workspace');
    expect(cardTitles()).toEqual(['Branding', 'Team', 'Custom Fields']);
  });
});

describe('SettingsPage — the nav is navigation, not a tablist', () => {
  it('renders anchors that carry the section in their href', async () => {
    auth.isAdmin = true;
    await render();
    const links = navLinks();
    expect(links).toHaveLength(4);
    for (const link of links) {
      expect(link.getAttribute('href')).toContain('section=');
      expect(link.getAttribute('tabindex')).toBeNull(); // natively focusable; no roving index
    }
  });

  it('marks exactly one link as the current page', async () => {
    auth.isAdmin = true;
    await render();
    expect(container.querySelectorAll('nav [aria-current="page"]')).toHaveLength(1);
    await clickTab('Integrations');
    const current = container.querySelectorAll('nav [aria-current="page"]');
    expect(current).toHaveLength(1);
    expect(current[0].textContent).toBe('Integrations');
  });

  it('promises no ARIA tab semantics it does not implement', async () => {
    auth.isAdmin = true;
    await render();
    expect(container.querySelectorAll('[role="tab"]')).toHaveLength(0);
    expect(container.querySelectorAll('[role="tablist"]')).toHaveLength(0);
  });

  it('gives the page a real h1 → h2 heading outline', async () => {
    await render();
    expect(container.querySelector('h1')!.textContent).toBe('Settings');
    const sections = container.querySelectorAll('section[aria-labelledby]');
    expect(sections).toHaveLength(cardTitles().length);
    for (const section of Array.from(sections)) {
      const labelId = section.getAttribute('aria-labelledby')!;
      expect(container.querySelector(`#${labelId}`)!.tagName).toBe('H2');
    }
  });

  it('never nests one card shell inside another', async () => {
    auth.isAdmin = true;
    await render();
    for (const label of navLabels()) {
      await clickTab(label!);
      expect(container.querySelectorAll('section section')).toHaveLength(0);
    }
  });
});

describe('SettingsPage — the Gmail OAuth callback', () => {
  it('selects Integrations for ?gmail=, toasts, strips the params, and stays put', async () => {
    auth.isAdmin = true;
    await render('/crm/settings?gmail=connected');

    // GmailCard only mounts — and can only toast/strip — if the page selected its section.
    expect(container.querySelector('[aria-current="page"]')!.textContent).toBe('Integrations');
    expect(api).toHaveBeenCalledWith('/api/gmail/status');
    expect(getToasts().map(t => t.message)).toContain('Gmail connected.');

    // The strip must not bounce the view back to the default section.
    expect(searchNow()).not.toContain('gmail');
    expect(searchNow()).toContain('section=integrations');
    expect(container.querySelector('[aria-current="page"]')!.textContent).toBe('Integrations');
  });

  it('surfaces a failure reason and still lands on Integrations', async () => {
    auth.isAdmin = true;
    await render('/crm/settings?gmail=error&reason=denied');
    expect(getToasts().map(t => t.message)).toContain('You declined the Google consent screen.');
    expect(container.querySelector('[aria-current="page"]')!.textContent).toBe('Integrations');
    expect(searchNow()).not.toContain('reason');
  });

  it('leaves a member on Personal and never toasts a connection they cannot see', async () => {
    // Unchanged from before #103: GmailCard has never rendered for a member, so nothing
    // consumes the param.
    await render('/crm/settings?gmail=connected');
    expect(container.querySelector('[aria-current="page"]')!.textContent).toBe('Personal');
    expect(getToasts()).toHaveLength(0);
  });
});

describe('SettingsPage — the role is never frozen', () => {
  it('grows the nav when isAdmin flips false→true, with no click', async () => {
    await render('/crm/settings?section=integrations');
    // A member asked for an admin section and was sent to the default.
    expect(navLabels()).toEqual(['Personal', 'Assistant']);
    expect(container.querySelector('[aria-current="page"]')!.textContent).toBe('Personal');

    auth.isAdmin = true;
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={['/crm/settings?section=integrations']}>
          <SettingsPage />
          <LocationProbe />
        </MemoryRouter>,
      );
    });

    expect(navLabels()).toEqual(['Personal', 'Assistant', 'Workspace', 'Integrations']);
    expect(container.querySelector('[aria-current="page"]')!.textContent).toBe('Integrations');
  });

  it('drops admin sections when isAdmin flips true→false in place', async () => {
    // Not hypothetical: ChangePasswordCard calls applyToken, which re-reads the live user
    // without unmounting this page — so a demotion by another admin lands mid-life.
    auth.isAdmin = true;
    await render('/crm/settings?section=workspace');
    expect(cardTitles()).toContain('Team');

    auth.isAdmin = false;
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={['/crm/settings?section=workspace']}>
          <SettingsPage />
          <LocationProbe />
        </MemoryRouter>,
      );
    });

    expect(navLabels()).toEqual(['Personal', 'Assistant']);
    expect(container.querySelector('[aria-current="page"]')!.textContent).toBe('Personal');
    for (const adminOnly of ADMIN_ONLY_TITLES) {
      expect(cardTitles()).not.toContain(adminOnly);
    }
  });
});

describe('SettingsPage — mobile', () => {
  it('pads every card down on small screens, in every section', async () => {
    auth.isAdmin = true;
    mobile.value = true;
    await render();
    for (const label of navLabels()) {
      await clickTab(label!);
      const sections = Array.from(container.querySelectorAll<HTMLElement>('section[aria-labelledby]'));
      expect(sections.length).toBeGreaterThan(0);
      for (const section of sections) {
        expect(section.style.padding).toBe('20px');
      }
    }
  });

  it('pads every card up on desktop, in every section', async () => {
    auth.isAdmin = true;
    await render();
    for (const label of navLabels()) {
      await clickTab(label!);
      const sections = Array.from(container.querySelectorAll<HTMLElement>('section[aria-labelledby]'));
      expect(sections.length).toBeGreaterThan(0);
      for (const section of sections) {
        expect(section.style.padding).toBe('28px');
      }
    }
  });
});
