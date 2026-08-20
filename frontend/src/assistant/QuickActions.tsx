// Record-aware starter prompts (issue #14). Rendered above the composer when a
// CRM record is open behind the drawer. Clicking a chip sends the prompt through
// the completely normal turn (context attaches in useAssistantChat).
//
// Phrasing rule: Gmail is read + create-draft ONLY — prompts may say "draft",
// never "send" (SECURITY.md trust guarantee).

import { ACCENT_TEXT, ACCENT_SOFT, INK_DIM, LINE } from '../shared/styles';
import type { ActiveRecordContext, ActiveRecordType } from './types';

// Sales-oriented starters (issue #22). Each maps to a working practice the assistant
// is instructed to follow in identity.SALES_GUIDE — recap before an interaction, always
// name a next step, log what happened, close deals with a reason — so a chip and the
// system prompt pull in the same direction. Generic by rule: no industry, product, or
// company wording may appear here (tests/test_prompt_genericization.py enforces it).
const STARTERS: Record<ActiveRecordType, string[]> = {
  deal: [
    'Summarize this deal',
    'What should my next step be on this deal?',
    'Draft a follow-up email about this deal',
    'Log a call I just had on this deal',
    // Replaces the narrower "Has this deal gone quiet?": crm_get_deal_health answers
    // that and the rest of the picture (score, stuck-in-stage, overdue, missing links).
    'How healthy is this deal?',
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
  ],
};

interface QuickActionsProps {
  record: ActiveRecordContext;
  onPick: (prompt: string) => void;
  disabled?: boolean;
}

export function QuickActions({ record, onPick, disabled }: QuickActionsProps) {
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{
        fontSize: 11, color: INK_DIM, marginBottom: 6,
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>
        Viewing: {record.label || `${record.recordType} #${record.recordId}`}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {STARTERS[record.recordType].map((p) => (
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
