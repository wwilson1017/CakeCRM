// @vitest-environment jsdom
//
// The edit sheet's two Copy buttons. `copyText.test.ts` proves the FORMATTER;
// this proves the WIRING, which is where the interesting mistake lives: each
// button must reach the clipboard with the right one of the two renderings, and
// both must read live form state rather than the saved row — copying a title
// you just retyped and getting the old one back is a silent wrong answer, and
// nothing in tsc or eslint can see the difference.
//
// createRoot + React 19 `act`, per AGENTS.md — no testing-library.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', () => ({
  createTodo: vi.fn(),
  updateTodo: vi.fn(),
  deleteTodo: vi.fn(),
}));

import { TodoEditSheet } from './TodoEditSheet';
import type { Todo, TodoProject } from '../types';

const TODO: Todo = {
  id: 7,
  title: 'Call the plumber about the upstairs sink',
  notes: 'He quoted $200 last time.',
  project_id: 3,
  project_name: 'House repairs',
  context: '@calls',
  tags: ['home'],
  status: 'next_action',
  star: true,
  due_date: '2026-08-15',
  repeat: 'weekly',
  auto_star_on_due: false,
  source: 'web',
  created_at: '2026-08-06T12:00:00Z',
  updated_at: '2026-08-06T12:00:00Z',
  completed_at: null,
  contact_id: null,
  deal_id: null,
};

const PROJECTS: TodoProject[] = [{
  id: 3, name: 'House repairs', notes: '', purpose: '', outcome: '', status: 'active',
  open_count: 2, created_at: '2026-08-01T12:00:00Z', updated_at: '2026-08-01T12:00:00Z',
}];

let container: HTMLDivElement;
let root: Root;
let written: string[];

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  written = [];
  // jsdom ships no clipboard; define the one method the hook calls.
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: (t: string) => { written.push(t); return Promise.resolve(); } },
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render(todo: Todo | null = TODO) {
  await act(async () => {
    root.render(
      <TodoEditSheet
        todo={todo}
        projects={PROJECTS}
        contexts={['@calls']}
        onClose={() => {}}
        onSaved={() => {}}
      />,
    );
  });
}

// Ends-with, because the accessible name gains the visible outcome word once
// there is one ("Copied — Copy the whole todo"), which is WCAG 2.5.3.
const copyButton = (label: string): HTMLButtonElement => {
  const found = container.querySelector<HTMLButtonElement>(`button[aria-label$="${label}"]`);
  if (!found) throw new Error(`no button labelled "${label}"`);
  return found;
};

const clickCopy = async (label: string) => {
  await act(async () => { copyButton(label).click(); });
};

const titleInput = () => container.querySelector<HTMLInputElement>('#gtd-title')!;

/** Type into the controlled title input the way React sees it. */
const typeTitle = async (value: string) => {
  const input = titleInput();
  const setter = Object.getOwnPropertyDescriptor(
    Object.getPrototypeOf(input), 'value',
  )?.set;
  await act(async () => {
    setter?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
};

describe('TodoEditSheet copy buttons', () => {
  it('copies just the action from the button beside the next-action label', async () => {
    await render();
    await clickCopy('Copy just the next action');
    expect(written).toEqual(['Call the plumber about the upstairs sink']);
  });

  it('copies the whole todo from the button beside the heading', async () => {
    await render();
    await clickCopy('Copy the whole todo');
    expect(written).toEqual([
      'Call the plumber about the upstairs sink\n'
      + '\n'
      + 'Status: Next\n'
      + 'Project: House repairs\n'
      + 'Context: @calls\n'
      + 'Due: 2026-08-15\n'
      + 'Repeat: Weekly\n'
      + 'Tags: home\n'
      + 'Starred: yes\n'
      + '\n'
      + 'Notes:\n'
      + 'He quoted $200 last time.',
    ]);
  });

  // Issue #151 asks for this button by name; neither blueprint ships one, so
  // nothing upstream pins it.
  it('copies just the notes from the button beside the notes label', async () => {
    await render();
    await clickCopy('Copy just the notes');
    expect(written).toEqual(['He quoted $200 last time.']);
  });

  it('copies what is on screen, not the saved row', async () => {
    await render();
    await typeTitle('Call the plumber about the DOWNSTAIRS sink');

    await clickCopy('Copy just the next action');
    expect(written).toEqual(['Call the plumber about the DOWNSTAIRS sink']);

    await clickCopy('Copy the whole todo');
    expect(written[1].split('\n')[0]).toBe('Call the plumber about the DOWNSTAIRS sink');
  });

  it('offers nothing to copy while the action is still blank', async () => {
    await render(null);
    expect(container.querySelector('button[aria-label$="Copy the whole todo"]')).toBeNull();
    expect(container.querySelector('button[aria-label$="Copy just the next action"]')).toBeNull();
    expect(container.querySelector('button[aria-label$="Copy just the notes"]')).toBeNull();

    await typeTitle('Buy milk');
    expect(copyButton('Copy the whole todo')).toBeTruthy();
    await clickCopy('Copy just the next action');
    expect(written).toEqual(['Buy milk']);
  });

  // The reason the shared hook grew a fallback: `/todo/{token}` is served over
  // plain http on a LAN install, where `navigator.clipboard` does not exist at
  // all. Without this path both buttons are dead controls on the one deployment
  // they were built for.
  it('still copies where there is no clipboard API at all', async () => {
    const execCopied: string[] = [];
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
    // Read the staging textarea the fallback mounts, which is what a real
    // execCommand('copy') would take the text from.
    (document as unknown as { execCommand: () => boolean }).execCommand = vi.fn(() => {
      // Read the FOCUSED element, exactly as the real execCommand('copy') does:
      // querying the staging textarea directly would pass even if the fallback
      // never focused it.
      const active = document.activeElement as HTMLTextAreaElement | null;
      execCopied.push(active?.tagName === 'TEXTAREA' ? active.value : '');
      return true;
    });

    await render();
    await clickCopy('Copy just the next action');

    expect(execCopied).toEqual(['Call the plumber about the upstairs sink']);
    // And it cleans up after itself rather than leaving a stray node behind.
    expect(document.querySelector('textarea[readonly]')).toBeNull();
  });

  // Where BOTH paths fail, the button has to say so. Staying on "Copy" leaves
  // the user unable to tell a failed copy from a tap that never registered —
  // and the outcome must reach a screen reader too, since the button's
  // accessible name is a fixed aria-label that does not change with the text.
  it('says so when the copy could not happen at all', async () => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
    (document as unknown as { execCommand: () => boolean }).execCommand = vi.fn(() => false);

    await render();
    await clickCopy('Copy just the next action');

    const button = copyButton('Copy just the next action');
    expect(button.textContent).toContain('Copy failed');
    // The accessible name carries the visible word too — a fixed label would
    // leave a voice-control user with nothing matching what is on screen.
    expect(button.getAttribute('aria-label')).toBe('Copy failed — Copy just the next action');
    // Scoped to THIS button's own region — each Copy button carries one, and
    // the other is still idle and empty.
    const status = button.parentElement?.querySelector('[role="status"]');
    expect(status?.textContent).toBe('Copy just the next action: Copy failed');
  });
});
