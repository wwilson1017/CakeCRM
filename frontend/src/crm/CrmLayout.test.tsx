// @vitest-environment jsdom
//
// Scope: only #102's task-mode ownership in the layout — which mode CrmLayout publishes
// for each demo-status outcome, and that the setter it provides actually reaches the
// context a route consumer reads.
//
// TaskModeCard.test.tsx pins the CARD's half of that contract against a stand-in owner;
// this file pins the real owner, so a change that stops passing `handleSetTaskMode` into
// the provider — or swaps the two fallback constants — fails here instead of silently
// keeping every other test green.
//
// The two fallbacks are DIFFERENT on purpose and that is the subtlest thing in #102:
//   failed fetch  → mode unknown → mirror the product default ('gtd'), the same answer
//                   the backend's get_task_mode() gives when it cannot read the row.
//   field absent  → a backend that predates #70 and has no GTD endpoints at all, where
//                   'normal' is the only mode that renders a working page.
// Nothing else about the layout (nav, onboarding, the AI nudge) is tested here.
import { useState } from 'react';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => vi.fn());
vi.mock('../core/api/client', () => ({ api }));
vi.mock('../core/auth/AuthContext', () => ({
  useAuth: () => ({ logout: vi.fn(), isAdmin: true }),
}));
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
const { useSetTaskMode, useTaskMode } = await import('./gtd/TaskModeContext');
import type { TaskMode } from './gtd/TaskModeContext';

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  api.mockReset();
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
 * Stands in for a real route under the layout's <Outlet/> — TasksModeRouter, in the app.
 * It reports exactly what that consumer would see, which is the value the layout owns.
 */
function Probe({ onSeen }: { onSeen?: (m: TaskMode | null) => void }) {
  const mode = useTaskMode();
  const setMode = useSetTaskMode();
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

async function renderLayout(onSeen?: (m: TaskMode | null) => void) {
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

describe('CrmLayout task-mode ownership (issue #102)', () => {
  it('publishes the mode the backend reports', async () => {
    api.mockResolvedValue({
      empty: false, sample_data_loaded: true, show_onboarding: false,
      ai_key_prompt_dismissed: true, task_mode: 'gtd',
    });
    await renderLayout();
    expect(publishedMode()).toBe('gtd');
  });

  it('falls back to the GTD product default when the demo-status fetch FAILS', async () => {
    api.mockRejectedValue(new Error('offline'));
    await renderLayout();
    // Mirrors the backend: get_task_mode() answers 'gtd' when it cannot read the row,
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
    // publishes null, which disables TaskModeCard's buttons. With no window in which a
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
      ai_key_prompt_dismissed: true, task_mode: 'gtd',
    });
    await renderLayout();
    expect(publishedMode()).toBe('gtd');

    await act(async () => {
      container.querySelector<HTMLButtonElement>('[data-testid="switch"]')!
        .dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    // The assertion that fails if the layout ever stops providing handleSetTaskMode:
    // the Settings card's switch has to be visible to /crm/tasks with no reload.
    expect(publishedMode()).toBe('normal');
  });
});
