// Record-aware starter prompts (issue #14). Rendered above the composer when a
// CRM record is open behind the drawer. Clicking a chip sends the prompt through
// the completely normal turn (context attaches in useAssistantChat).
//
// Phrasing rule: Gmail is read + create-draft ONLY — prompts may say "draft",
// never "send" (SECURITY.md trust guarantee).

import { ACCENT_TEXT, ACCENT_SOFT, INK_DIM, LINE } from '../shared/styles';
import { SETTINGS_SECTIONS, type SettingsSectionId } from '../crm/settingsSections';
import type { ActiveRecordContext, ActiveRecordType, SettingsPageContext } from './types';

// Sales-oriented starters (issue #22). Each maps to a working practice the assistant
// is instructed to follow in identity.SALES_GUIDE — recap before an interaction, always
// name a next step, log what happened, close deals with a reason — so a chip and the
// system prompt pull in the same direction. Generic by rule: no industry, product, or
// company wording may appear here (tests/test_prompt_genericization.py enforces it).
//
// The last chip on the deal and company contexts is a COACHING starter (issue #201):
// it asks for the advisory turn identity.COACHING_GUIDE describes — read the health,
// staleness and analytics signals first, then lead with the highest-leverage move —
// rather than for a summary or a write. It sits last so the everyday actions stay
// first under the thumb.
const STARTERS: Record<ActiveRecordType, string[]> = {
  deal: [
    'Summarize this deal',
    'What should my next step be on this deal?',
    'Draft a follow-up email about this deal',
    'Log a call I just had on this deal',
    // Replaces the narrower "Has this deal gone quiet?": crm_get_deal_health answers
    // that and the rest of the picture (score, stuck-in-stage, overdue, missing links).
    'How healthy is this deal?',
    'What is my plan to win this deal?',
  ],
  contact: [
    'Summarize this contact',
    'Catch me up before I call them',
    'Draft a follow-up email to this contact',
    'What deals do I have with this contact?',
    'Log a call with this contact',
  ],
  company: [
    'Summarize this company',
    'What deals and contacts are linked to this company?',
    'Which deals here need attention?',
    'Draft a follow-up email to a contact at this company',
    'Add a note about this company',
    'Where should I focus in this company?',
  ],
};

// Settings-section starters (issue #200 — help manual phase 2). Every chip is a
// question the built-in manual answers, so a click lands on help_search →
// help_read_topic rather than on general CRM folklore. The last chip in each section is
// the same one everywhere on purpose: it is the one question the manual CANNOT answer,
// because it is about this install rather than about the product, and it is what
// crm_get_setup_status exists for.
//
// Same generic-prose rule as STARTERS above (tests/test_prompt_genericization.py).
const SETTINGS_STARTERS: Record<SettingsSectionId, string[]> = {
  personal: [
    'How do notifications work?',
    'How do I set up two-factor authentication?',
    'What is set up on this install?',
  ],
  assistant: [
    'How does your long-term memory work?',
    'What is the difference between the todo modes?',
    'What is set up on this install?',
  ],
  workspace: [
    'How do custom fields work?',
    'How do I add someone to the team?',
    'How do I change the branding?',
    'What is set up on this install?',
  ],
  integrations: [
    'How do I connect Gmail?',
    'What can you do with my email?',
    'How do I set up Telegram?',
    'What is set up on this install?',
  ],
};

const SECTION_LABELS: Record<string, string> = Object.fromEntries(
  SETTINGS_SECTIONS.map((s) => [s.id, s.label]),
);

interface QuickActionsProps {
  /** CRM record open behind the drawer. */
  record?: ActiveRecordContext | null;
  /** Settings section open behind the drawer (#200). */
  page?: SettingsPageContext | null;
  onPick: (prompt: string) => void;
  disabled?: boolean;
}

/** Nothing open behind the drawer → no chips (the caller renders nothing either).
 *  A record wins over a page when both are somehow present: it is the more specific
 *  thing to be looking at, and the record starters are about the work rather than
 *  about the product. */
function starters(record?: ActiveRecordContext | null, page?: SettingsPageContext | null):
  { heading: string; prompts: string[] } | null {
  if (record) {
    return {
      heading: `Viewing: ${record.label || `${record.recordType} #${record.recordId}`}`,
      prompts: STARTERS[record.recordType],
    };
  }
  if (page) {
    return {
      heading: `Settings — ${SECTION_LABELS[page.section] ?? page.section}`,
      prompts: SETTINGS_STARTERS[page.section],
    };
  }
  return null;
}

export function QuickActions({ record, page, onPick, disabled }: QuickActionsProps) {
  const picked = starters(record, page);
  if (!picked) return null;
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{
        fontSize: 11, color: INK_DIM, marginBottom: 6,
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>
        {picked.heading}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {picked.prompts.map((p) => (
          <button
            key={p}
            onClick={() => onPick(p)}
            disabled={disabled}
            style={{
              fontSize: 12, padding: '4px 10px', borderRadius: 999,
              border: `1px solid ${LINE}`, background: ACCENT_SOFT, color: ACCENT_TEXT,
              cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.5 : 1,
              fontFamily: 'inherit',
            }}
          >
            {p}
          </button>
        ))}
      </div>
    </div>
  );
}
