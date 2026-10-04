// The settings-page context the drawer reports (issue #200).
//
// The property under test is AGREEMENT: whatever `SettingsPage` shows, this must name.
// That page's shown section is `resolveSection(wantedSection(params), isAdmin)`, and so is
// this — so every case below is really asking whether reusing those two helpers, rather
// than re-deriving the rule, holds under the awkward inputs (a member on an admin section,
// the Gmail callback's sectionless URL, a junk `?section=`).

import { describe, expect, it } from 'vitest';

import { resolveSection, wantedSection, SETTINGS_SECTIONS } from '../crm/settingsSections';
import { SETTINGS_PATH, TODO_REVIEW_PATH, pageContextFor, settingsPageContext } from './pageContext';

const ctx = (url: string, isAdmin = true) => {
  const { pathname, search } = new URL(url, 'https://example.test');
  return settingsPageContext(pathname, new URLSearchParams(search), isAdmin);
};

describe('settingsPageContext', () => {
  it('names the section the URL asks for', () => {
    expect(ctx(`${SETTINGS_PATH}?section=workspace`)).toEqual({
      page: 'settings', section: 'workspace',
    });
  });

  it('answers for every section the Settings page declares', () => {
    // Not a loop for its own sake: this is what keeps the backend Literal, the section
    // registry and the chip table describing one set. A section nobody can report is a
    // section the assistant is never told about.
    for (const section of SETTINGS_SECTIONS) {
      expect(ctx(`${SETTINGS_PATH}?section=${section.id}`)).toEqual({
        page: 'settings', section: section.id,
      });
    }
  });

  it('falls back to the default section with no query at all', () => {
    expect(ctx(SETTINGS_PATH)).toEqual({ page: 'settings', section: 'personal' });
  });

  it('reports the section a MEMBER actually lands on, not the one they asked for', () => {
    // Workspace is admin-only, so `resolveSection` drops a member to the default. Telling
    // the assistant "you are in Workspace" while the page shows Personal is the exact
    // disagreement deriving from the URL exists to prevent.
    const asked = 'workspace';
    expect(resolveSection(asked, false)).not.toBe(asked);
    expect(ctx(`${SETTINGS_PATH}?section=${asked}`, false)).toEqual({
      page: 'settings', section: 'personal',
    });
  });

  it('follows the Gmail OAuth callback to Integrations for an admin', () => {
    // Google lands on /crm/settings?gmail=connected with no `section`, and `wantedSection`
    // maps that to Integrations so GmailCard mounts. The drawer must agree.
    expect(wantedSection(new URLSearchParams('gmail=connected'))).toBe('integrations');
    expect(ctx(`${SETTINGS_PATH}?gmail=connected`)).toEqual({
      page: 'settings', section: 'integrations',
    });
  });

  it('does not put a member on Integrations for that same callback', () => {
    // A member never mounts GmailCard, so they never see that section either.
    expect(ctx(`${SETTINGS_PATH}?gmail=connected`, false)).toEqual({
      page: 'settings', section: 'personal',
    });
  });

  it('ignores an unknown or mis-cased section rather than forwarding it', () => {
    for (const bad of ['billing', 'Workspace', '', 'workspace ']) {
      expect(ctx(`${SETTINGS_PATH}?section=${encodeURIComponent(bad)}`)).toEqual({
        page: 'settings', section: 'personal',
      });
    }
  });

  it('is null anywhere but the Settings page', () => {
    for (const path of ['/crm/pipeline', '/crm/contacts', '/crm', '/crm/settings/extra', '/']) {
      expect(ctx(path)).toBeNull();
    }
  });

  it('carries no client free text — only the page and section literals', () => {
    const value = ctx(`${SETTINGS_PATH}?section=integrations&label=IGNORE+PREVIOUS`);
    expect(Object.keys(value ?? {}).sort()).toEqual(['page', 'section']);
    expect(value?.page).toBe('settings');
  });
});

describe('pageContextFor (#263)', () => {
  const at = (url: string) => {
    const { pathname, search } = new URL(url, 'https://example.test');
    return pageContextFor(pathname, new URLSearchParams(search), true);
  };

  it('names the todo Review page', () => {
    expect(at(TODO_REVIEW_PATH)).toEqual({ page: 'todo_review' });
  });

  it('still names the settings section', () => {
    expect(at(`${SETTINGS_PATH}?section=workspace`)).toEqual({ page: 'settings', section: 'workspace' });
  });

  it('names nothing on any other todo page', () => {
    expect(at('/crm/todos/inbox')).toBeNull();
    expect(at('/crm/todos')).toBeNull();
  });
});
