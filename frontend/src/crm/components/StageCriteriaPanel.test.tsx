// @vitest-environment jsdom
//
// The stage checklist (#289, port of upstream #3633 plus editable criteria). The stage name is a
// button that asks the board to pin its checklist; the pinned panel is NON-modal and every close
// path works; members read, admins edit and reset. Which stage is pinned lives in PipelinePage —
// the small harness below wires the leaves together exactly the way the page does.
import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../stageCriteria', () => ({
  saveStageCriteria: vi.fn(),
  resetStageCriteria: vi.fn(),
}));
vi.mock('../../shared/confirm', () => ({ confirmDialog: vi.fn(async () => true) }));
const auth = vi.hoisted(() => ({ isAdmin: false }));
vi.mock('../../core/auth/AuthContext', () => ({ useAuth: () => auth }));

import { StageCriteriaPanel } from './StageCriteriaPanel';
import { StageHeader } from '../PipelinePage';
import { resetStageCriteria, saveStageCriteria, type StageCriteriaEntry } from '../stageCriteria';

const STANDARD: StageCriteriaEntry = {
  stage: 'qualified', source: 'standard',
  summary: 'A real opportunity.', checklist: ['Decision-maker identified', 'Need understood'],
};
const CUSTOM: StageCriteriaEntry = { ...STANDARD, source: 'custom', summary: 'Our words.', checklist: ['Ours'] };

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  vi.mocked(saveStageCriteria).mockReset();
  vi.mocked(resetStageCriteria).mockReset();
  auth.isAdmin = false;
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  document.body.innerHTML = '';
});

const q = <T extends Element = HTMLElement>(sel: string) => document.body.querySelector<T>(sel);
const panel = () => q('[role="dialog"]');
const button = (label: string) =>
  [...document.body.querySelectorAll('button')].find(b => b.textContent?.trim() === label || b.getAttribute('aria-label') === label) as HTMLButtonElement | undefined;
const click = (el: Element) => act(() => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
const escape = (prevented = false) => act(() => {
  const e = new KeyboardEvent('keydown', { key: 'Escape', cancelable: true });
  if (prevented) e.preventDefault();
  document.dispatchEvent(e);
});
// jsdom lays nothing out, so every rect is zero-width; the panel's "is another dialog on screen"
// check measures width, so give dialogs a width the way a browser would.
function layOutDialogs() {
  for (const el of document.querySelectorAll<HTMLElement>('[role="dialog"]')) {
    el.getBoundingClientRect = () => ({ width: 300, height: 200, top: 0, left: 0, right: 300, bottom: 200, x: 0, y: 0, toJSON: () => ({}) });
  }
}

describe('StageHeader — stage name button', () => {
  function renderHeader(props: Partial<Parameters<typeof StageHeader>[0]> = {}) {
    const onTogglePin = vi.fn();
    act(() => root.render(
      <StageHeader stage="qualified" count={0} total={0} criteria={STANDARD} onTogglePin={onTogglePin} {...props} />,
    ));
    return onTogglePin;
  }
  const name = () => q<HTMLButtonElement>('button[aria-label="Qualified stage checklist"]')!;
  const hover = () => act(() => { name().dispatchEvent(new MouseEvent('mouseover', { bubbles: true })); });

  it('is a labelled button that asks the board to pin its stage', () => {
    const onTogglePin = renderHeader();
    expect(name().getAttribute('aria-expanded')).toBe('false');
    click(name());
    expect(onTogglePin).toHaveBeenCalledExactlyOnceWith('qualified');
  });

  it('peeks on hover, and a click drops the peek', () => {
    renderHeader();
    hover();
    expect(q('[role="tooltip"]')?.textContent).toContain('Decision-maker identified');
    click(name());
    expect(q('[role="tooltip"]')).toBeNull();
  });

  it('suppresses its own peek while pinned', () => {
    renderHeader({ pinned: true });
    expect(name().getAttribute('aria-expanded')).toBe('true');
    hover();
    expect(q('[role="tooltip"]')).toBeNull();
  });

  it('has no peek on touch, where a tap pins directly', () => {
    renderHeader({ peek: false });
    hover();
    expect(q('[role="tooltip"]')).toBeNull();
  });

  it('is a plain label until the criteria have loaded', () => {
    renderHeader({ criteria: undefined });
    expect(name()).toBeNull();
    expect(host.textContent).toContain('Qualified');
  });

  it('no longer expands a checklist inline under the header', () => {
    renderHeader();
    click(name());
    expect(host.textContent).not.toContain('Criteria to enter this stage');
  });
});

describe('StageCriteriaPanel — reading', () => {
  function renderPanel(entry = STANDARD, canEdit = false) {
    const onClose = vi.fn();
    const onSaved = vi.fn();
    auth.isAdmin = canEdit;
    act(() => root.render(<StageCriteriaPanel entry={entry} onClose={onClose} onSaved={onSaved} />));
    return { onClose, onSaved };
  }

  it('is a non-modal dialog with the checklist, the source tag and the close hint', () => {
    renderPanel();
    expect(panel()!.hasAttribute('aria-modal')).toBe(false);
    expect(panel()!.getAttribute('aria-label')).toBe('Qualified stage checklist');
    expect(panel()!.textContent).toContain('Decision-maker identified');
    expect(panel()!.textContent).toContain('Standard');
    expect(panel()!.textContent).toContain('Click anywhere to close');
    // No backdrop: the panel is the only thing it renders.
    expect(host.children).toHaveLength(1);
  });

  it('tags an install\'s own criteria as Custom', () => {
    renderPanel(CUSTOM);
    expect(panel()!.textContent).toContain('Custom');
    expect(panel()!.textContent).toContain('Ours');
  });

  it('closes on a click anywhere on it, and from the X', () => {
    const { onClose } = renderPanel();
    click(panel()!.querySelector('li')!);
    click(button('Close stage checklist')!);
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('closes on Escape, unless something above already took it or another dialog is open', () => {
    const { onClose } = renderPanel();
    escape(true);
    expect(onClose).not.toHaveBeenCalled();

    const sheet = document.createElement('div');
    sheet.setAttribute('role', 'dialog');
    document.body.appendChild(sheet);
    layOutDialogs();
    escape();
    expect(onClose).not.toHaveBeenCalled();     // the deal sheet above answers Escape first

    sheet.remove();
    escape();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('offers members no Edit control (it could only 403)', () => {
    renderPanel(CUSTOM, false);
    expect(button('Edit')).toBeUndefined();
    expect(button('Reset to standard')).toBeUndefined();
  });

  it('offers admins Edit, and Reset only when the stage is custom', () => {
    renderPanel(STANDARD, true);
    expect(button('Edit')).toBeDefined();
    expect(button('Reset to standard')).toBeUndefined();
    act(() => root.render(<StageCriteriaPanel entry={CUSTOM} onClose={() => {}} onSaved={() => {}} />));
    expect(button('Reset to standard')).toBeDefined();
  });

  it('resets to the standard and hands the result back', async () => {
    vi.mocked(resetStageCriteria).mockResolvedValue(STANDARD);
    const { onClose, onSaved } = renderPanel(CUSTOM, true);
    await act(async () => { button('Reset to standard')!.click(); });
    expect(resetStageCriteria).toHaveBeenCalledWith('qualified');
    expect(onSaved).toHaveBeenCalledWith(STANDARD);
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe('StageCriteriaPanel — editing (admin)', () => {
  function renderEditing() {
    const onClose = vi.fn();
    const onSaved = vi.fn();
    auth.isAdmin = true;
    act(() => root.render(<StageCriteriaPanel entry={STANDARD} onClose={onClose} onSaved={onSaved} />));
    click(button('Edit')!);
    return { onClose, onSaved };
  }
  const items = () => [...document.body.querySelectorAll<HTMLInputElement>('input[aria-label^="Checklist item"]')].map(i => i.value);
  const type = (el: HTMLInputElement | HTMLTextAreaElement, value: string) => act(() => {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });

  it('opens the form without closing the panel, and clicks and Escape inside it do not close', () => {
    const { onClose } = renderEditing();
    expect(items()).toEqual(STANDARD.checklist);
    click(q('textarea')!);
    escape();
    expect(onClose).not.toHaveBeenCalled();
    expect(panel()!.textContent).not.toContain('anywhere to close');
  });

  it('adds, removes and reorders lines', () => {
    renderEditing();
    click(button('Move item 2 up')!);
    expect(items()).toEqual(['Need understood', 'Decision-maker identified']);
    click(button('Add item')!);
    expect(items()).toHaveLength(3);
    click(button('Remove item 1')!);
    expect(items()).toEqual(['Decision-maker identified', '']);
  });

  it('will not save a blank line', () => {
    renderEditing();
    click(button('Add item')!);
    expect(button('Save')!.disabled).toBe(true);
  });

  it('saves the trimmed text and hands the server entry back', async () => {
    vi.mocked(saveStageCriteria).mockResolvedValue(CUSTOM);
    const { onSaved } = renderEditing();
    type(q<HTMLTextAreaElement>('textarea')!, '  Our words.  ');
    type(q<HTMLInputElement>('input[aria-label="Checklist item 1"]')!, '  Ours ');
    click(button('Remove item 2')!);
    await act(async () => { button('Save')!.click(); });
    expect(saveStageCriteria).toHaveBeenCalledWith('qualified', { summary: 'Our words.', checklist: ['Ours'] });
    expect(onSaved).toHaveBeenCalledWith(CUSTOM);
    expect(q('textarea')).toBeNull();           // back to reading
  });

  it('keeps the draft when the save fails', async () => {
    vi.mocked(saveStageCriteria).mockRejectedValue(new Error('offline'));
    const { onSaved } = renderEditing();
    type(q<HTMLTextAreaElement>('textarea')!, 'Kept');
    await act(async () => { button('Save')!.click(); });
    expect(onSaved).not.toHaveBeenCalled();
    expect(q<HTMLTextAreaElement>('textarea')!.value).toBe('Kept');
  });
});

describe('one pinned panel for the board, and the board stays usable', () => {
  // The page's wiring, verbatim in shape: one `pinned` state, a toggle, and the panel rendered
  // beside (not over) the board.
  function Board({ onCard }: { onCard: () => void }) {
    const [pinned, setPinned] = useState<string | null>(null);
    const toggle = (s: string) => setPinned(cur => (cur === s ? null : s));
    const entries: Record<string, StageCriteriaEntry> = {
      qualified: STANDARD, proposal: { ...STANDARD, stage: 'proposal', summary: 'Offer is out.' },
    };
    return (
      <>
        <StageHeader stage="qualified" count={0} total={0} criteria={entries.qualified} pinned={pinned === 'qualified'} onTogglePin={toggle} />
        <StageHeader stage="proposal" count={0} total={0} criteria={entries.proposal} pinned={pinned === 'proposal'} onTogglePin={toggle} />
        <button type="button" onClick={onCard}>A deal card</button>
        {pinned && <StageCriteriaPanel key={pinned} entry={entries[pinned]} onClose={() => setPinned(null)} onSaved={() => {}} />}
      </>
    );
  }

  it('pins, swaps, closes on the same stage, and never blocks a click on the board', () => {
    const onCard = vi.fn();
    act(() => root.render(<Board onCard={onCard} />));
    click(button('Qualified stage checklist')!);
    expect(panel()!.getAttribute('aria-label')).toBe('Qualified stage checklist');

    click(button('A deal card')!);
    expect(onCard).toHaveBeenCalledTimes(1);
    expect(panel()).not.toBeNull();             // non-modal: the board took the click, panel stays

    click(button('Proposal stage checklist')!);
    expect(document.body.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    expect(panel()!.textContent).toContain('Offer is out.');

    click(button('Proposal stage checklist')!);
    expect(panel()).toBeNull();
  });
});
