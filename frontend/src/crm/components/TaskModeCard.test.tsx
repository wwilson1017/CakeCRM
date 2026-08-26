// @vitest-environment jsdom
//
// Scope: only #102's ownership change — the card reads and writes the task mode through
// the contexts CrmLayout owns, instead of keeping a local copy.
//
// This is the regression that mattered: before #102 the card held its own `mode` state,
// so switching in Settings left the layout's TaskModeContext stale and /crm/tasks kept
// rendering the OLD task system until a full page reload. #102 made GTD the default for
// every install, which makes "switch back in Settings" the opt-out — so it has to work
// on the spot. A local copy would still make the BUTTON look right, which is exactly why
// the assertion below is on the OWNER's state, not on the card's own rendering.
//
// The no-login surfaces half of the card is #70's and is not re-tested here.
import { useState } from 'react';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => vi.fn());
vi.mock('../../core/api/client', () => ({ api }));
vi.mock('../../shared/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const { TaskModeCard } = await import('./TaskModeCard');
const { TaskModeContext, TaskModeSetterContext } = await import('../gtd/TaskModeContext');
type TaskMode = 'normal' | 'gtd';

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  api.mockReset();
  // The card's only fetch is the no-login surfaces; reject so that half stays hidden
  // and this test is about the mode buttons alone.
  api.mockRejectedValue(new Error('not under test'));
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

/**
 * Stands in for CrmLayout: it owns the mode and publishes both halves, exactly as the
 * real layout does. `seen` records what a consumer under the provider (TasksModeRouter,
 * in the app) would observe — that is the value the old local-state bug left stale.
 */
function Owner({ initial, seen }: { initial: TaskMode; seen: TaskMode[] }) {
  const [mode, setMode] = useState<TaskMode>(initial);
  seen.push(mode);
  return (
    <TaskModeContext.Provider value={mode}>
      <TaskModeSetterContext.Provider value={setMode}>
        <TaskModeCard isMobile={false} />
      </TaskModeSetterContext.Provider>
    </TaskModeContext.Provider>
  );
}

function modeButtons(): HTMLButtonElement[] {
  return [...container.querySelectorAll('button[aria-pressed]')] as HTMLButtonElement[];
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
}

describe('TaskModeCard mode ownership (issue #102)', () => {
  it('leads with Todo-GTD as the default and offers the simple list as the opt-out', async () => {
    const seen: TaskMode[] = [];
    await act(async () => root.render(<Owner initial="gtd" seen={seen} />));

    const [first, second] = modeButtons();
    expect(first.textContent).toContain('Todo-GTD');
    expect(first.textContent).toContain('default');
    expect(second.textContent).toContain('Simple list');
    // The card reflects the context it was given rather than a fetch of its own.
    expect(first.getAttribute('aria-pressed')).toBe('true');
    expect(second.getAttribute('aria-pressed')).toBe('false');
  });

  it('writes the switch through to the OWNER, so /crm/tasks changes with no reload', async () => {
    const seen: TaskMode[] = [];
    await act(async () => root.render(<Owner initial="gtd" seen={seen} />));

    api.mockResolvedValueOnce({ ok: true, task_mode: 'normal' });
    await click(modeButtons()[1]);

    expect(api).toHaveBeenCalledWith('/api/crm/task-mode', {
      method: 'POST',
      body: JSON.stringify({ mode: 'normal' }),
    });
    // The assertion that fails if the card ever keeps its own copy again.
    expect(seen.at(-1)).toBe('normal');
    expect(modeButtons()[1].getAttribute('aria-pressed')).toBe('true');
  });

  it('leaves the owner untouched when the switch request fails', async () => {
    const seen: TaskMode[] = [];
    await act(async () => root.render(<Owner initial="gtd" seen={seen} />));

    api.mockRejectedValueOnce(new Error('boom'));
    await click(modeButtons()[1]);

    expect(seen.at(-1)).toBe('gtd');
  });

  it('disables both buttons until the mode is known', async () => {
    await act(async () => root.render(
      <TaskModeContext.Provider value={null}>
        <TaskModeSetterContext.Provider value={() => {}}>
          <TaskModeCard isMobile={false} />
        </TaskModeSetterContext.Provider>
      </TaskModeContext.Provider>,
    ));
    expect(modeButtons().every(b => b.disabled)).toBe(true);
  });
});
