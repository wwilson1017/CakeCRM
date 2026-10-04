// @vitest-environment jsdom
//
// The drawer's starter chips (issue #14, settings sections added by #200).
//
// Every settings section must HAVE chips, and they must be about THAT section — a section
// that renders an empty strip, or one offering another section's questions, is a section
// the manual is never usefully consulted about. Whether each chip's wording actually
// matches a manual topic is prose, and prose truth is not mechanically testable; what IS
// pinned is that the one chip the manual cannot answer — "what is set up on this install",
// which `crm_get_setup_status` exists for — is offered from everywhere.

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { QuickActions } from './QuickActions';
import { SETTINGS_SECTIONS, type SettingsSectionId } from '../crm/settingsSections';
import type { ActiveRecordContext, PageContext, SettingsPageContext } from './types';

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(props: {
  record?: ActiveRecordContext | null;
  page?: PageContext | null;
  onPick?: (p: string) => void;
}) {
  act(() => {
    root.render(<QuickActions onPick={props.onPick ?? (() => {})} {...props} />);
  });
}

const chips = () => [...container.querySelectorAll('button')].map(b => b.textContent ?? '');
const heading = () => container.querySelector('div > div')?.textContent ?? '';

const settings = (section: SettingsSectionId): SettingsPageContext => ({ page: 'settings', section });

describe('settings-section chips', () => {
  it('offers chips for every section the Settings page declares', () => {
    for (const section of SETTINGS_SECTIONS) {
      render({ page: settings(section.id) });
      expect(chips().length, `section ${section.id} renders no chips`).toBeGreaterThan(0);
    }
  });

  it('names the section the user is looking at', () => {
    render({ page: settings('workspace') });
    expect(heading()).toContain('Workspace');
  });

  it('asks the install-status question from every section', () => {
    // The one chip the manual cannot answer — it is about this install, not the product.
    for (const section of SETTINGS_SECTIONS) {
      render({ page: settings(section.id) });
      expect(chips()).toContain('What is set up on this install?');
    }
  });

  it('asks about the section it is in', () => {
    render({ page: settings('integrations') });
    expect(chips().join(' ')).toMatch(/Gmail|Telegram/);
    render({ page: settings('workspace') });
    expect(chips().join(' ')).toMatch(/custom fields|team|branding/i);
  });

  it('never offers to send mail', () => {
    // Gmail is read + create-draft only, forever (SECURITY.md). A chip may say "draft",
    // never "send" — the same rule the record starters above it follow.
    for (const section of SETTINGS_SECTIONS) {
      render({ page: settings(section.id) });
      for (const chip of chips()) expect(chip.toLowerCase()).not.toContain('send');
    }
  });

  it('sends the chip text as a normal turn', () => {
    const onPick = vi.fn();
    render({ page: settings('personal'), onPick });
    act(() => { (container.querySelector('button') as HTMLButtonElement).click(); });
    expect(onPick).toHaveBeenCalledWith(chips()[0]);
  });
});

describe('what the strip chooses to show', () => {
  it('renders nothing with neither a record nor a page', () => {
    render({});
    expect(container.innerHTML).toBe('');
  });

  it('keeps the record starters unchanged when a record is open', () => {
    render({ record: { recordType: 'deal', recordId: 7, label: 'Acme renewal' } });
    expect(heading()).toContain('Acme renewal');
    expect(chips()).toContain('Summarize this deal');
  });

  it('prefers the record when somehow both are present', () => {
    // The two are mutually exclusive by routing today (no detail page renders under
    // /crm/settings). If that ever changes, the record is the more specific thing to be
    // looking at, and its starters are about the work rather than about the product.
    render({
      record: { recordType: 'contact', recordId: 3 },
      page: settings('integrations'),
    });
    expect(chips()).toContain('Summarize this contact');
    expect(chips()).not.toContain('What is set up on this install?');
  });
});

describe('the todo Review page chips (#263)', () => {
  it('offers to start the weekly review', () => {
    render({ page: { page: 'todo_review' } });
    expect(heading()).toContain('Weekly review');
    expect(chips()[0]).toBe('Start my weekly review');
  });

  it('sends the chip as a normal turn', () => {
    const onPick = vi.fn();
    render({ page: { page: 'todo_review' }, onPick });
    act(() => { (container.querySelector('button') as HTMLButtonElement).click(); });
    expect(onPick).toHaveBeenCalledWith('Start my weekly review');
  });
});
