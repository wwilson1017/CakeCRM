// @vitest-environment jsdom
//
// The phone bottom tab bar (#266). What a render can see is the active-tab state and the
// More disclosure; the breakpoint (`sm:hidden`) and the safe-area padding are CSS, which
// jsdom does not apply — the 390x844 screenshot on the PR is the record for those.
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TodoShell } from '../TodoShell';
import type { TodoTab } from '../tabs';

vi.mock('../api', () => ({
  getFilters: vi.fn().mockResolvedValue({ contexts: [], tags: [], status_counts: {} }),
  listProjects: vi.fn().mockResolvedValue([]),
  createTodo: vi.fn(),
  updateTodo: vi.fn(),
}));
import { BottomBar } from './BottomBar';

const PRIMARY_TABS: [TodoTab, string][] = [
  ['inbox', 'Inbox'], ['today', 'Today'], ['next', 'To Do'], ['projects', 'Projects'],
];
const MORE_LABELS = ['Contexts', 'Waiting', 'Someday', 'Review', 'Done'];

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

beforeEach(() => {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

function render(active: TodoTab, inboxCount = 0) {
  act(() => {
    root.render(
      <MemoryRouter>
        <BottomBar active={active} inboxCount={inboxCount} />
      </MemoryRouter>,
    );
  });
}

const current = () =>
  [...host.querySelectorAll('[aria-current="page"]')].map(el => el.textContent);
const moreButton = () => host.querySelector('button[aria-expanded]') as HTMLButtonElement;

describe('BottomBar', () => {
  it('shows the four primary lists plus More, in the issue order', () => {
    render('today');
    const nav = host.querySelector('nav[aria-label="Main lists"]')!;
    expect([...nav.children].map(c => c.textContent)).toEqual(
      ['Inbox', 'Today', 'To Do', 'Projects', 'More'],
    );
    expect(nav.classList.contains('ck-has-bottom-bar')).toBe(true);
  });

  it.each(PRIMARY_TABS)(
    'marks %s, and only it, as the current page',
    (key, label) => {
      render(key);
      expect(current()).toEqual([label]);
      expect(moreButton().className).toContain('border-transparent');
    },
  );

  it('lets More stand in for a current page that lives behind it', () => {
    render('waiting');
    expect(current()).toEqual([]);
    expect(moreButton().className).toContain('border-brand');
    act(() => moreButton().click());
    expect(moreButton().getAttribute('aria-expanded')).toBe('true');
    expect(current()).toEqual(['Waiting']);
    // Every secondary list is reachable from the phone.
    const panel = document.getElementById(moreButton().getAttribute('aria-controls')!)!;
    expect([...panel.querySelectorAll('a')].map(a => a.textContent)).toEqual(MORE_LABELS);
  });

  it('closes More on Escape and returns focus to the button', () => {
    render('today');
    act(() => moreButton().click());
    act(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(moreButton().getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).toBe(moreButton());
  });

  it('carries the inbox badge', () => {
    render('today', 3);
    const inbox = [...host.querySelectorAll('a')].find(a => a.textContent?.startsWith('Inbox'))!;
    expect(inbox.textContent).toBe('Inbox3');
  });
});

// The other half of the scope rule: under CrmLayout the bar would collide with the CRM's
// own nav and the Baker launcher. No base is injected here, so this is the CRM mount.
// (The public mount's bar is asserted through the real boot in PublicTodoApp.test.tsx.)
describe('TodoShell in the CRM', () => {
  it('renders no bottom bar and keeps the top tab strip', () => {
    act(() => {
      root.render(
        <MemoryRouter>
          <TodoShell active="today" hideQuickAdd>page</TodoShell>
        </MemoryRouter>,
      );
    });
    expect(host.querySelector('nav[aria-label="Main lists"]')).toBeNull();
    expect(host.querySelector('.ck-has-bottom-bar')).toBeNull();
    expect(host.textContent).toContain('Waiting');
  });
});
