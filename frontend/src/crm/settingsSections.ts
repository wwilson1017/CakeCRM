/**
 * settingsSections — the Settings page's information architecture, in one place (#103).
 *
 * Settings used to be nine cards appended to a single chain in merge order. This module
 * replaces that order with four sections and makes ONE declaration authoritative for both
 * "what goes where" and "who may see it".
 *
 * Why the gating lives here rather than at nine call sites: every install-configuration
 * route is admin-only since #60, so rendering those controls to a member would offer an
 * action that can only 403. The server gate is the real one — this is about not offering
 * what cannot work. Section visibility is DERIVED from card visibility (a section shows
 * iff at least one of its cards does), so an all-admin group can never render as an empty
 * section or a dead tab for a member. That is a property of the derivation, not a flag
 * somebody has to remember to keep in sync.
 *
 * Section ORDER is load-bearing: the two member-visible sections come first, so a member's
 * nav is a strict PREFIX of an admin's. When `isAdmin` flips false→true (the window right
 * after login, while /api/me is still in flight) the strip only grows at the end and the
 * section already on screen never moves.
 */

export type SettingsSectionId = 'personal' | 'assistant' | 'workspace' | 'integrations';

export type SettingsCardId =
  | 'notifications' | 'change_password'
  | 'memory' | 'task_mode'
  | 'branding' | 'team' | 'custom_fields'
  | 'telegram' | 'gmail';

export interface SettingsCardDef {
  id: SettingsCardId;
  /** Hidden from members. Mirrors `require_admin` on the routes the card drives. */
  adminOnly: boolean;
}

export interface SettingsSection {
  id: SettingsSectionId;
  label: string;
  cards: readonly SettingsCardDef[];
}

export const SETTINGS_SECTIONS: readonly SettingsSection[] = [
  {
    id: 'personal',
    label: 'Personal',
    cards: [
      { id: 'notifications', adminOnly: false },
      { id: 'change_password', adminOnly: false },
    ],
  },
  {
    id: 'assistant',
    label: 'Assistant',
    cards: [
      { id: 'memory', adminOnly: false },
      { id: 'task_mode', adminOnly: false },
    ],
  },
  {
    id: 'workspace',
    label: 'Workspace',
    cards: [
      { id: 'branding', adminOnly: true },
      { id: 'team', adminOnly: true },
      { id: 'custom_fields', adminOnly: true },
    ],
  },
  {
    id: 'integrations',
    label: 'Integrations',
    cards: [
      { id: 'telegram', adminOnly: true },
      { id: 'gmail', adminOnly: true },
    ],
  },
];

/** Role-independent on purpose: the landing section must be visible to both roles. */
export const DEFAULT_SECTION: SettingsSectionId = SETTINGS_SECTIONS[0].id;

/**
 * The section that owns the Gmail card. Google's OAuth callback lands on
 * `/crm/settings?gmail=…` carrying no `section`, and only a mounted GmailCard can toast
 * the result and strip those params — so a bare `gmail` param has to select this section.
 * Exported so GmailCard can put the URL back in a coherent state without a magic string.
 */
export const GMAIL_SECTION: SettingsSectionId = 'integrations';

export function visibleCards(section: SettingsSection, isAdmin: boolean): SettingsCardDef[] {
  return section.cards.filter(c => !c.adminOnly || isAdmin);
}

export function visibleSections(isAdmin: boolean): SettingsSection[] {
  return SETTINGS_SECTIONS.filter(s => visibleCards(s, isAdmin).length > 0);
}

/** Exact match only — an unknown or mis-cased value is not a section. */
export function parseSection(raw: string | null): SettingsSectionId | null {
  return SETTINGS_SECTIONS.some(s => s.id === raw) ? (raw as SettingsSectionId) : null;
}

/** Falls back rather than 404s: a member deep-linked to an admin section lands on the default. */
export function resolveSection(wanted: SettingsSectionId, isAdmin: boolean): SettingsSectionId {
  return visibleSections(isAdmin).some(s => s.id === wanted) ? wanted : DEFAULT_SECTION;
}

/**
 * Which section this URL is asking for, before the role is applied.
 *
 * `gmail` wins over an explicit `section` because the toast must fire and the params must
 * be stripped — otherwise a stale result re-toasts on the next visit. GmailCard's cleanup
 * writes `section=integrations` back, so the URL and the view agree afterwards.
 */
export function wantedSection(params: URLSearchParams): SettingsSectionId {
  if (params.has('gmail')) return GMAIL_SECTION;
  return parseSection(params.get('section')) ?? DEFAULT_SECTION;
}
