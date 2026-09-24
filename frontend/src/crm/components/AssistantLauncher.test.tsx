// @vitest-environment jsdom
//
// The launcher's relationship with an open detail panel, which is the ONE place it is allowed to
// sit above an overlay — and the one place that permission can lose a user's work.
//
// `DetailModal`'s `underLauncher` mode renders the centred panel at `dock:z-[39]`, BELOW this
// button, so a record detail can hand its context to the assistant drawer (#14). That exemption
// is about the drawer. On a keyless install there is no drawer: the button reads "Hire your
// assistant" and NAVIGATES to /setup, which unmounts the open panel and any inline edit draft in
// it without ever reaching that panel's close guard. So the exemption — both halves of it, the
// z-order and the Tab-cycle hand-off — is granted only when clicking really does open a drawer.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => vi.fn());
vi.mock('../../core/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../core/api/client')>()),
  api,
}));
// `useAuth` throws outside `AuthProvider`, and the launcher reads `isAdmin` to resolve
// the settings section it reports to the drawer (#200). Mocked per the repo idiom
// (`CrmLayout.test.tsx`) rather than wrapping every case in a provider that fetches.
const isAdmin = vi.hoisted(() => ({ value: true }));
vi.mock('../../core/auth/AuthContext', () => ({
  useAuth: () => ({ isAdmin: isAdmin.value }),
}));

// The drawer body is lazy and heavy; stubbed so the props it is handed can be read
// without booting the chat surface.
const panelProps = vi.hoisted(() => ({ value: undefined as unknown }));
vi.mock('../../assistant/AssistantPanelBody', () => ({
  default: (props: unknown) => { panelProps.value = props; return null; },
}));

const { AssistantLauncher } = await import('./AssistantLauncher');
const { MemoryRouter } = await import('react-router-dom');
const { ActiveRecordProvider } = await import('../RecordContext');

/** The centred `underLauncher` panel's z-index — the number this button must straddle. */
const PANEL_Z = 39;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }),
  });
  api.mockReset();
  api.mockResolvedValue({});
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(aiReady: boolean | null, url = '/crm/pipeline') {
  act(() => {
    root.render(
      <MemoryRouter initialEntries={[url]}>
        <ActiveRecordProvider>
          <AssistantLauncher aiReady={aiReady} />
        </ActiveRecordProvider>
      </MemoryRouter>,
    );
  });
}

/** The launcher itself, found by role rather than by the attribute under test. */
const launcher = () =>
  [...container.querySelectorAll('button')]
    .find(b => b.getAttribute('aria-haspopup') === 'dialog') as HTMLButtonElement;

const zOf = (el: HTMLElement) => Number(el.style.zIndex);

describe('the launcher over an open detail panel', () => {
  it('sits ABOVE the panel and joins its Tab cycle when it opens the drawer', () => {
    render(true);

    expect(zOf(launcher())).toBeGreaterThan(PANEL_Z);
    expect(launcher().hasAttribute('data-detail-companion')).toBe(true);
  });

  it('sits BELOW the panel and stays out of its Tab cycle when it only navigates', () => {
    // Keyless: the button is a "Hire your assistant" CTA that routes to /setup. A navigation
    // unmounts the panel, so leaving it clickable over one discards an inline edit draft with no
    // prompt — the exact loss the panel's close guard exists to prevent, reached around it.
    render(false);

    expect(launcher().textContent).toContain('Hire your assistant');
    expect(zOf(launcher())).toBeLessThan(PANEL_Z);
    expect(launcher().hasAttribute('data-detail-companion')).toBe(false);
  });

  it('stays below while the AI state is still unknown', () => {
    // `aiReady === null` renders the button disabled, so it can open no drawer and can take no
    // focus — handing a dialog's Tab cycle to it would end the cycle on a control that cannot
    // accept it.
    render(null);

    expect(launcher().disabled).toBe(true);
    expect(zOf(launcher())).toBeLessThan(PANEL_Z);
    expect(launcher().hasAttribute('data-detail-companion')).toBe(false);
  });
});

describe('the settings section behind the drawer (#200)', () => {
  // The chips and the per-turn prompt note both key off this prop, so a launcher that
  // reported a different section from the one on screen would be worse than none.
  async function panel(url: string, admin: boolean) {
    isAdmin.value = admin;
    panelProps.value = undefined;
    render(true, url);
    // The body is behind React.lazy + Suspense; let the stubbed import resolve.
    await act(async () => { await Promise.resolve(); });
    return panelProps.value as { pageContext?: { page: string; section: string } | null };
  }

  it('hands the drawer the section the URL names', async () => {
    expect((await panel('/crm/settings?section=integrations', true)).pageContext)
      .toEqual({ page: 'settings', section: 'integrations' });
  });

  it('hands it the section a MEMBER actually lands on', async () => {
    expect((await panel('/crm/settings?section=workspace', false)).pageContext)
      .toEqual({ page: 'settings', section: 'personal' });
  });

  it('hands it nothing off the Settings page', async () => {
    expect((await panel('/crm/pipeline', true)).pageContext).toBeNull();
  });
});

// ── The fold ────────────────────────────────────────────────────────────────
//
// The pill reads its full label for `LAUNCHER_INTRO_MS` after every (re)label, then folds to
// the icon; it unfolds while the pointer is within `LAUNCHER_REACH_PX` of it or it holds focus.
const { LAUNCHER_INTRO_MS, LAUNCHER_REACH_PX } = await import('./AssistantLauncher');

const expanded = () => launcher().getAttribute('data-expanded') === 'true';

/** Park the button at a known box so pointer distances mean something under jsdom. */
function placeLauncher() {
  launcher().getBoundingClientRect = () =>
    ({ left: 900, right: 1000, top: 700, bottom: 752, width: 100, height: 52, x: 900, y: 700, toJSON: () => ({}) }) as DOMRect;
}

function pointerAt(clientX: number, clientY: number) {
  // jsdom has no PointerEvent constructor; a MouseEvent under the pointer name carries the
  // coordinates and an undefined `pointerType`, which is what a mouse looks like to the handler.
  act(() => { document.dispatchEvent(new MouseEvent('pointermove', { clientX, clientY, bubbles: true })); });
}

describe('the launcher fold', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it('shows the full label on load, then folds to the icon', () => {
    render(true);
    expect(expanded()).toBe(true);

    act(() => { vi.advanceTimersByTime(LAUNCHER_INTRO_MS - 1); });
    expect(expanded()).toBe(true);
    act(() => { vi.advanceTimersByTime(1); });
    expect(expanded()).toBe(false);
  });

  it('restarts the intro when the label changes, so "Ask Baker" gets its full showing', () => {
    // `aiReady` resolves a beat after mount: the placeholder "Baker" spends the intro, then the
    // real call-to-action arrives — and must not arrive already folded.
    render(null);
    act(() => { vi.advanceTimersByTime(LAUNCHER_INTRO_MS); });
    expect(expanded()).toBe(false);

    render(true);
    expect(launcher().getAttribute('aria-label')).toBe('Ask Baker');
    expect(expanded()).toBe(true);
    act(() => { vi.advanceTimersByTime(LAUNCHER_INTRO_MS); });
    expect(expanded()).toBe(false);
  });

  it('unfolds as the pointer approaches and folds again as it leaves', () => {
    render(true);
    act(() => { vi.advanceTimersByTime(LAUNCHER_INTRO_MS); });
    placeLauncher();
    expect(expanded()).toBe(false);

    pointerAt(900 - LAUNCHER_REACH_PX + 1, 726);   // just inside reach, to the left
    expect(expanded()).toBe(true);
    pointerAt(900 - LAUNCHER_REACH_PX - 1, 726);   // just outside
    expect(expanded()).toBe(false);
    pointerAt(950, 726);                           // directly over it
    expect(expanded()).toBe(true);
    pointerAt(100, 100);
    expect(expanded()).toBe(false);
  });

  it('unfolds while it holds keyboard focus', () => {
    render(true);
    act(() => { vi.advanceTimersByTime(LAUNCHER_INTRO_MS); });
    expect(expanded()).toBe(false);

    act(() => { launcher().focus(); });
    expect(expanded()).toBe(true);
    act(() => { launcher().blur(); });
    expect(expanded()).toBe(false);
  });
});
