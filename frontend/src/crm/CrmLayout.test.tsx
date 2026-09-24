// @vitest-environment jsdom
//
// Scope: only #102's todo-mode ownership in the layout — which mode CrmLayout publishes
// for each demo-status outcome, and that the setter it provides actually reaches the
// context a route consumer reads.
//
// TodoModeCard.test.tsx pins the CARD's half of that contract against a stand-in owner;
// this file pins the real owner, so a change that stops passing `handleSetTodoMode` into
// the provider — or swaps the two fallback constants — fails here instead of silently
// keeping every other test green.
//
// The two fallbacks are DIFFERENT on purpose and that is the subtlest thing in #102:
//   failed fetch  → mode unknown → mirror the product default ('gtd'), the same answer
//                   the backend's get_todo_mode() gives when it cannot read the row.
//   field absent  → a backend that predates #70 and has no GTD endpoints at all, where
//                   'normal' is the only mode that renders a working page.
// Nothing else about the layout (nav, onboarding, the AI nudge) is tested here, EXCEPT
// #192's push-subscription self-heal: the layout is where it is triggered, and the
// migration's decision not to claim legacy push endpoints depends on it firing once per
// signed-in seat. pushSubscription.test.ts pins what the function itself does.
import { useState } from 'react';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => vi.fn());
vi.mock('../core/api/client', () => ({ api }));
const currentUser = vi.hoisted(() => ({ value: { id: 7 } as { id: number } | null }));
vi.mock('../core/auth/AuthContext', () => ({
  useAuth: () => ({ logout: vi.fn(), isAdmin: true, currentUser: currentUser.value }),
}));
const resyncPushSubscription = vi.hoisted(() => vi.fn());
vi.mock('../core/notifications/pushSubscription', () => ({ resyncPushSubscription }));
vi.mock('../core/branding/BrandingContext', () => ({
  useBranding: () => ({ branding: { company_name: 'Test Co' }, logoVersion: 0 }),
}));
// Chrome that would fetch on its own — not under test, and each would otherwise consume
// the shared `api` mock and make the assertions below depend on call ordering.
vi.mock('./components/NotificationsBell', () => ({ NotificationsBell: () => null }));
vi.mock('./components/AssistantLauncher', () => ({ AssistantLauncher: () => null }));
vi.mock('./components/BrandLogo', () => ({ BrandLogo: () => null }));
vi.mock('./components/ThemeToggle', () => ({ ThemeToggle: () => null }));

const { CrmLayout } = await import('./CrmLayout');
const { useSetTodoMode, useTodoMode } = await import('./gtd/TodoModeContext');
import type { TodoMode } from './gtd/TodoModeContext';

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  api.mockReset();
  resyncPushSubscription.mockReset();
  currentUser.value = { id: 7 };
  // jsdom ships no matchMedia, and useIsMobile calls it on mount. Stubbed per-file
  // rather than in a shared setup file, per the repo's "opt in per test file" rule.
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
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

/**
 * Stands in for a real route under the layout's <Outlet/> — TodosModeRouter, in the app.
 * It reports exactly what that consumer would see, which is the value the layout owns.
 */
function Probe({ onSeen }: { onSeen?: (m: TodoMode | null) => void }) {
  const mode = useTodoMode();
  const setMode = useSetTodoMode();
  const [clicked, setClicked] = useState(false);
  onSeen?.(mode);
  return (
    <div>
      <span data-testid="mode">{mode ?? 'unknown'}</span>
      <button
        type="button"
        data-testid="switch"
        onClick={() => { setMode('normal'); setClicked(true); }}
      >
        {clicked ? 'clicked' : 'switch'}
      </button>
    </div>
  );
}

async function renderLayout(onSeen?: (m: TodoMode | null) => void) {
  await act(async () => root.render(
    <MemoryRouter initialEntries={['/crm']}>
      <Routes>
        <Route path="/crm" element={<CrmLayout />}>
          <Route index element={<Probe onSeen={onSeen} />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  ));
}

function publishedMode(): string {
  return container.querySelector('[data-testid="mode"]')?.textContent ?? '';
}

describe('CrmLayout todo-mode ownership (issue #102)', () => {
  it('publishes the mode the backend reports', async () => {
    api.mockResolvedValue({
      empty: false, sample_data_loaded: true, show_onboarding: false,
      ai_key_prompt_dismissed: true, todo_mode: 'gtd',
    });
    await renderLayout();
    expect(publishedMode()).toBe('gtd');
  });

  it('falls back to the GTD product default when the demo-status fetch FAILS', async () => {
    api.mockRejectedValue(new Error('offline'));
    await renderLayout();
    // Mirrors the backend: get_todo_mode() answers 'gtd' when it cannot read the row,
    // so the client must not guess 'normal' and disagree with it.
    expect(publishedMode()).toBe('gtd');
  });

  it('falls back to normal when the field is ABSENT from a successful response', async () => {
    // A backend that predates #70 has no GTD endpoints at all — 'gtd' here would render
    // a shell whose every request 404s. Deliberately NOT the failed-fetch answer above.
    api.mockResolvedValue({
      empty: false, sample_data_loaded: true, show_onboarding: false,
      ai_key_prompt_dismissed: true,
    });
    await renderLayout();
    expect(publishedMode()).toBe('normal');
  });

  it('publishes NOTHING while the demo-status fetch is still in flight', async () => {
    // This is what closes the switch-while-loading race, and it was disputed twice in
    // review — so it is pinned here rather than argued. `status` starts null and
    // DEMO_STATUS_UNKNOWN is reachable ONLY from the .catch, so the pending state
    // publishes null, which disables TodoModeCard's buttons. With no window in which a
    // switch can be made, there is no window in which a late GET can overwrite one.
    //
    // If someone ever seeds `useState` with DEMO_STATUS_UNKNOWN instead of null, this
    // fails — and the race becomes real.
    api.mockReturnValue(new Promise(() => { /* never settles */ }));
    await renderLayout();
    expect(publishedMode()).toBe('unknown');
  });

  it('routes a mode switch from a child straight into the published context', async () => {
    api.mockResolvedValue({
      empty: false, sample_data_loaded: true, show_onboarding: false,
      ai_key_prompt_dismissed: true, todo_mode: 'gtd',
    });
    await renderLayout();
    expect(publishedMode()).toBe('gtd');

    await act(async () => {
      container.querySelector<HTMLButtonElement>('[data-testid="switch"]')!
        .dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    // The assertion that fails if the layout ever stops providing handleSetTodoMode:
    // the Settings card's switch has to be visible to /crm/todos with no reload.
    expect(publishedMode()).toBe('normal');
  });
});

// #149's route-chunk boundary. CrmLayout wraps <Outlet /> in its own Suspense so a page chunk
// loading — or the todo mode flipping from unknown to 'gtd', which is a plain setState and not
// a router transition — suspends only the content column.
//
// `bootSplit.test.ts` pins that positionally, in source. This pins the BEHAVIOUR, because the
// two failures are different: source order says where the tags are, this says what a user sees
// while a chunk is in flight. A boundary hoisted to wrap the whole layout body keeps the Outlet
// nested inside it and would replace the entire authenticated shell — nav, sign-out, launcher —
// with one spinner on every first visit to a page.
describe('CrmLayout route Suspense boundary (#149)', () => {
  it('keeps the nav mounted while a lazy route chunk is still loading', async () => {
    api.mockResolvedValue({
      empty: false, sample_data_loaded: true, show_onboarding: false,
      ai_key_prompt_dismissed: true, todo_mode: 'gtd',
    });

    // A route element that is genuinely pending: `lazy()` over a promise we resolve by hand,
    // so the suspended state is observable rather than a race we hope to catch.
    let release!: (value: { default: () => React.ReactElement }) => void;
    const pending = new Promise<{ default: () => React.ReactElement }>((resolve) => { release = resolve; });
    const { lazy } = await import('react');
    const LazyRoute = lazy(() => pending);

    await act(async () => root.render(
      <MemoryRouter initialEntries={['/crm']}>
        <Routes>
          <Route path="/crm" element={<CrmLayout />}>
            <Route index element={<LazyRoute />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    ));

    // Suspended: the content column shows the fallback…
    expect(container.querySelector('[role="status"]')).not.toBeNull();
    expect(container.textContent).not.toContain('ROUTE CONTENT');
    // …and the chrome around it is still on screen. This is the assertion a hoisted boundary
    // fails, and the one the positional guard in bootSplit.test.ts cannot make.
    expect(container.textContent, 'nav survives a suspended route').toContain('Dashboard');
    expect(container.textContent, 'sign-out survives a suspended route').toContain('Sign out');

    await act(async () => {
      release({ default: () => <div>ROUTE CONTENT</div> });
      await pending;
    });

    // Resolved: content replaces the fallback, chrome unchanged.
    expect(container.textContent).toContain('ROUTE CONTENT');
    expect(container.querySelector('[role="status"]')).toBeNull();
    expect(container.textContent).toContain('Dashboard');
  });
});

describe('CrmLayout push-subscription self-heal (issue #192)', () => {
  beforeEach(() => {
    api.mockResolvedValue({
      empty: false, sample_data_loaded: true, show_onboarding: false,
      ai_key_prompt_dismissed: true, todo_mode: 'gtd',
    });
  });

  it('re-stamps this browser onto the signed-in seat on load', async () => {
    await renderLayout();
    expect(resyncPushSubscription).toHaveBeenCalledTimes(1);
  });

  it('does not fire while nobody is signed in', async () => {
    // currentUser is null during the auth bootstrap. Re-POSTing then would stamp the
    // endpoint with whatever the expiring session was, which is the state the self-heal
    // exists to get out of.
    currentUser.value = null;
    await renderLayout();
    expect(resyncPushSubscription).not.toHaveBeenCalled();
  });

  it('fires again when the signed-in account changes', async () => {
    // The cross-tab BroadcastChannel login swap in AuthContext does not remount this
    // layout, so a mount-only effect would leave the endpoint bound to the previous
    // occupant of a shared browser.
    await renderLayout();
    expect(resyncPushSubscription).toHaveBeenCalledTimes(1);
    currentUser.value = { id: 9 };
    await renderLayout();
    expect(resyncPushSubscription).toHaveBeenCalledTimes(2);
  });
});
