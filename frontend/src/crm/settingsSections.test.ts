// Node environment (no docblock needed — `node` is the vitest default).
//
// What this pins is the Settings information architecture's two load-bearing properties,
// neither of which is visible by reading one card:
//
//   1. The member/admin PARTITION — members see exactly the four member-visible cards and
//      never an install-configuration one. Pinned in both directions, so widening the
//      member set is as much a failure as narrowing the admin set.
//   2. The PREFIX property — a member's section list is the first N of an admin's. That is
//      what makes the post-login `isAdmin` false→true flip append tabs instead of inserting
//      one before the section already on screen.
//
// Plus a source guard: the shared card shell only prevents double-padding if every card
// actually REPLACED its old wrapper. A DOM assertion cannot see that (the old wrappers are
// `<div>`s, so a forgotten one still renders as `section > div` and looks fine to a
// selector) — but the import can, and it is deterministic.
import { describe, expect, it } from 'vitest';

import {
  DEFAULT_SECTION, GMAIL_SECTION, SETTINGS_SECTIONS,
  parseSection, resolveSection, visibleCards, visibleSections, wantedSection,
  type SettingsCardId,
} from './settingsSections';

const MEMBER_CARDS: SettingsCardId[] = [
  'notifications', 'change_password', 'pipeline_board', 'memory',
  // #193. Linking YOUR phone is personal; the bot token stays admin-only below.
  'telegram_link',
];
const ADMIN_ONLY_CARDS: SettingsCardId[] = [
  'branding', 'team', 'custom_fields', 'telegram', 'gmail',
  // #102 made the task-mode + todo-surface routes require_admin.
  'todo_mode',
];

function visibleCardIds(isAdmin: boolean): SettingsCardId[] {
  return visibleSections(isAdmin).flatMap(s => visibleCards(s, isAdmin)).map(c => c.id);
}

describe('settingsSections — the member/admin partition', () => {
  it('shows a member exactly the member-visible cards', () => {
    expect(visibleCardIds(false).sort()).toEqual([...MEMBER_CARDS].sort());
  });

  it('shows an admin all eleven cards, each exactly once', () => {
    const ids = visibleCardIds(true);
    expect(ids).toHaveLength(11);
    expect(new Set(ids).size).toBe(11);
    expect(ids.sort()).toEqual([...MEMBER_CARDS, ...ADMIN_ONLY_CARDS].sort());
  });

  it('never leaks an install-configuration card to a member', () => {
    const memberIds = visibleCardIds(false);
    for (const adminOnly of ADMIN_ONLY_CARDS) {
      expect(memberIds).not.toContain(adminOnly);
    }
  });

  it('offers a member their own Telegram link while the bot stays admin-only', () => {
    // #193 dissolved the link-code redaction by dissolving its premise: a per-seat code
    // claims the caller's OWN row, so there is nothing an admin gate would protect.
    // Connecting the workspace's bot is still install configuration.
    const memberIds = visibleCardIds(false);
    expect(memberIds).toContain('telegram_link');
    expect(memberIds).not.toContain('telegram');
    const personal = SETTINGS_SECTIONS.find(s => s.id === 'personal');
    expect(personal?.cards.map(c => c.id)).toContain('telegram_link');
  });

  it('never hides a personal card from an admin', () => {
    const adminIds = visibleCardIds(true);
    for (const shared of MEMBER_CARDS) {
      expect(adminIds).toContain(shared);
    }
  });
});

describe('settingsSections — section derivation', () => {
  it('gives a member only the two sections that have visible cards', () => {
    expect(visibleSections(false).map(s => s.id)).toEqual(['personal', 'assistant']);
  });

  it('gives an admin all four sections in declaration order', () => {
    expect(visibleSections(true).map(s => s.id))
      .toEqual(['personal', 'assistant', 'workspace', 'integrations']);
  });

  it('never yields a section with zero visible cards, for either role', () => {
    for (const isAdmin of [false, true]) {
      for (const section of visibleSections(isAdmin)) {
        expect(visibleCards(section, isAdmin).length).toBeGreaterThan(0);
      }
    }
  });

  it("keeps the member's sections a strict prefix of the admin's", () => {
    const memberIds = visibleSections(false).map(s => s.id);
    const adminIds = visibleSections(true).map(s => s.id);
    expect(adminIds.slice(0, memberIds.length)).toEqual(memberIds);
    expect(adminIds.length).toBeGreaterThan(memberIds.length);
  });

  it('defaults to a section a member can actually see', () => {
    expect(visibleSections(false).map(s => s.id)).toContain(DEFAULT_SECTION);
  });
});

describe('settingsSections — URL parsing', () => {
  it('accepts an exact section id and rejects anything else', () => {
    expect(parseSection('workspace')).toBe('workspace');
    expect(parseSection('Workspace')).toBeNull();
    expect(parseSection('nope')).toBeNull();
    expect(parseSection(null)).toBeNull();
  });

  it('falls a member back to the default rather than showing an admin section', () => {
    expect(resolveSection('workspace', false)).toBe(DEFAULT_SECTION);
    expect(resolveSection('integrations', false)).toBe(DEFAULT_SECTION);
    expect(resolveSection('workspace', true)).toBe('workspace');
  });

  it('reads the wanted section off the query string', () => {
    expect(wantedSection(new URLSearchParams(''))).toBe(DEFAULT_SECTION);
    expect(wantedSection(new URLSearchParams('section=assistant'))).toBe('assistant');
    expect(wantedSection(new URLSearchParams('section=bogus'))).toBe(DEFAULT_SECTION);
  });

  it('lets a bare ?gmail= select Integrations, even over an explicit section', () => {
    // Google's callback carries no `section`, and only a mounted GmailCard can toast the
    // result and clear the params — so `gmail` has to win, or a stale result re-toasts on
    // the next visit.
    expect(wantedSection(new URLSearchParams('gmail=connected'))).toBe(GMAIL_SECTION);
    expect(wantedSection(new URLSearchParams('gmail=error&reason=denied'))).toBe(GMAIL_SECTION);
    expect(wantedSection(new URLSearchParams('section=personal&gmail=error'))).toBe(GMAIL_SECTION);
  });

  it('ignores an empty ?gmail=, matching GmailCard\'s own early return', () => {
    // The two must agree on what counts as a callback. If this honoured a bare key while
    // the card skipped it, the URL would select a section nothing ever cleans up.
    expect(wantedSection(new URLSearchParams('gmail='))).toBe(DEFAULT_SECTION);
    expect(wantedSection(new URLSearchParams('section=assistant&gmail='))).toBe('assistant');
  });
});

describe('settingsSections — every card has a home', () => {
  it('declares each card id exactly once across all sections', () => {
    const ids = SETTINGS_SECTIONS.flatMap(s => s.cards).map(c => c.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids).toHaveLength(11);
  });

  it('gives every section a non-empty label', () => {
    for (const section of SETTINGS_SECTIONS) {
      expect(section.label.trim().length).toBeGreaterThan(0);
    }
  });
});

describe('SettingsCard is the only owner of card chrome', () => {
  // The shell can only guarantee "no double padding, no unpadded card" if each card
  // REPLACED its outer wrapper instead of nesting inside the shell. `cardStyle` is the
  // fingerprint of that wrapper, and it has legitimate consumers elsewhere in the app —
  // so this is scoped to the settings cards by name.
  //
  // Read through Vite's `?raw` glob rather than `node:fs` because `tsconfig.app.json`
  // sets `types: ["vite/client"]`, so a node builtin would not type-check under `tsc -b`.
  const SOURCES = import.meta.glob('./components/*.tsx', {
    query: '?raw', import: 'default', eager: true,
  }) as Record<string, string>;

  // filename → the registry id that file must render as. A card id does not mechanically
  // map to a filename, so this is hand-maintained — the count check below is what stops
  // it drifting from the registry.
  const CARD_FILE_IDS: Record<string, SettingsCardId> = {
    'BrandingCard.tsx': 'branding',
    'ChangePasswordCard.tsx': 'change_password',
    'CustomFieldSettings.tsx': 'custom_fields',
    'GmailCard.tsx': 'gmail',
    'MemoryCard.tsx': 'memory',
    'NotificationSettings.tsx': 'notifications',
    'PipelineBoardCard.tsx': 'pipeline_board',
    'TaskModeCard.tsx': 'todo_mode',
    'TeamSettings.tsx': 'team',
    'TelegramLinkCard.tsx': 'telegram_link',
    'TelegramSettings.tsx': 'telegram',
  };
  const SETTINGS_CARD_FILES = Object.keys(CARD_FILE_IDS);

  const sourceOf = (file: string): string => {
    const source = SOURCES[`./components/${file}`];
    // A rename must fail loudly — silently reading `undefined` would make this guard
    // pass forever on a file it is no longer looking at.
    if (source === undefined) throw new Error(`No source globbed for ${file}`);
    return source;
  };

  it('maps every registry card to exactly one file, and no file to a stale id', () => {
    // Hand-maintained by necessity — a card id ('custom_fields') does not mechanically
    // map to a filename ('CustomFieldSettings.tsx'). Both halves are needed: the length
    // catches a card added to the registry but not here, and the SET equality catches a
    // copy-pasted duplicate value (which keeps the length right while silently leaving
    // one registry id bound to no file at all).
    const declared = SETTINGS_SECTIONS.flatMap(s => s.cards).map(c => c.id);
    expect(SETTINGS_CARD_FILES).toHaveLength(declared.length);
    expect(new Set(Object.values(CARD_FILE_IDS))).toEqual(new Set(declared));
  });

  it.each(SETTINGS_CARD_FILES)('%s does not import cardStyle', (file) => {
    expect(sourceOf(file)).not.toMatch(/\bcardStyle\b/);
  });

  it.each(SETTINGS_CARD_FILES)('%s takes isMobile as a prop rather than calling the hook', (file) => {
    // The page already knows the viewport and passes it down, so every card reads it the
    // same way. A card reaching for `useIsMobile` itself is invisible to the page test —
    // its module-level mock answers every caller identically — so it is pinned here.
    expect(sourceOf(file)).not.toMatch(/\buseIsMobile\b/);
    expect(sourceOf(file)).toMatch(/isMobile/);
  });

  it.each(SETTINGS_CARD_FILES)('%s renders under its own registry id', (file) => {
    // `SettingsCardId` constrains the `id` prop to ONE OF the registered ids, never to the one this
    // component is registered as — so a copy-pasted `id` type-checks, renders, and mints a
    // duplicate DOM id plus an ambiguous `aria-labelledby` target. Bind it here.
    expect(sourceOf(file)).toMatch(new RegExp(`id="${CARD_FILE_IDS[file]}"`));
  });

  it('renders its own chrome from cardStyle', () => {
    expect(sourceOf('SettingsCard.tsx')).toMatch(/\bcardStyle\b/);
  });
});
