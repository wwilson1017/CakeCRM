/**
 * SettingsPage — the Settings shell: a section nav, and the active section's cards (#103).
 *
 * This page used to BE the list — nine cards appended in merge order, each with its own
 * layout habits. Now it owns three things and nothing else: the heading, the nav, and the
 * 620 px column the cards sit in. Which cards exist, where they live and who may see them
 * is declared once in `settingsSections.ts`; how a card looks is `components/SettingsCard.tsx`.
 *
 * The nav is `<Link>`s in a `<nav>`, not an ARIA tablist — these tabs navigate (they write
 * the URL and create history entries), which is the "underline tabs for navigation" side of
 * the repo's tab rule and gets the native keyboard model for free. The active section is
 * therefore a pure function of the URL and the role, recomputed every render: nothing
 * freezes `isAdmin`, so the post-login false→true flip just appends tabs (member sections
 * are ordered first precisely so the strip grows at the end and the visible section
 * never moves).
 *
 * `?gmail=…` is the one query contract that is not ours: Google's OAuth callback lands on
 * /crm/settings?gmail=connected|error with no `section`, and only a MOUNTED GmailCard can
 * toast that result and clear the params. So a bare `gmail` selects Integrations
 * (`wantedSection`), and GmailCard's existing cleanup writes `section=integrations` back as
 * it strips — which is why removing the params cannot bounce the view to the default.
 * A member never mounts GmailCard and so never strips the param, exactly as before.
 */

import type { ComponentType } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { useAuth } from '../core/auth/AuthContext';
import { useIsMobile } from '../shared/useIsMobile';
import { INK, INK_MUTE, LINE, ACCENT } from '../shared/styles';
import { pagePadding, pageHeading } from './styles';
import {
  resolveSection, visibleCards, visibleSections, wantedSection,
  type SettingsCardId, type SettingsSection, type SettingsSectionId,
} from './settingsSections';
import { BrandingCard } from './components/BrandingCard';
import { ChangePasswordCard } from './components/ChangePasswordCard';
import { CustomFieldSettings } from './components/CustomFieldSettings';
import { GmailCard } from './components/GmailCard';
import { MemoryCard } from './components/MemoryCard';
import { NotificationSettings } from './components/NotificationSettings';
import { TaskModeCard } from './components/TaskModeCard';
import { TeamSettings } from './components/TeamSettings';
import { TelegramSettings } from './components/TelegramSettings';

// A total Record over the card-id union: adding an id to the registry without a component
// here is a compile error, not a blank space on the page.
const CARD_COMPONENTS: Record<SettingsCardId, ComponentType<{ isMobile: boolean }>> = {
  notifications: NotificationSettings,
  change_password: ChangePasswordCard,
  memory: MemoryCard,
  task_mode: TaskModeCard,
  branding: BrandingCard,
  team: TeamSettings,
  custom_fields: CustomFieldSettings,
  telegram: TelegramSettings,
  gmail: GmailCard,
};

const COLUMN_MAX_WIDTH = 620;

function SettingsNav({ sections, shown, isMobile, params }: {
  sections: SettingsSection[];
  shown: SettingsSectionId;
  isMobile: boolean;
  params: URLSearchParams;
}) {
  return (
    <nav
      aria-label="Settings sections"
      style={{
        display: 'flex', gap: isMobile ? 0 : 4,
        borderBottom: `1px solid ${LINE}`,
        marginBottom: isMobile ? 16 : 24,
        overflowX: isMobile ? 'auto' : undefined,
        WebkitOverflowScrolling: 'touch',
      }}
    >
      {sections.map(section => {
        const isActive = section.id === shown;
        // Preserve any other param the URL is carrying; only `section` is ours.
        const next = new URLSearchParams(params);
        next.set('section', section.id);
        return (
          <Link
            key={section.id}
            to={{ search: `?${next}` }}
            aria-current={isActive ? 'page' : undefined}
            style={{
              fontSize: isMobile ? 13 : 15,
              padding: isMobile ? '8px 10px' : '8px 14px',
              whiteSpace: 'nowrap', textDecoration: 'none',
              color: isActive ? INK : INK_MUTE,
              borderBottom: `2px solid ${isActive ? ACCENT : 'transparent'}`,
              marginBottom: -1,
            }}
          >
            {section.label}
          </Link>
        );
      })}
    </nav>
  );
}

export function SettingsPage() {
  const { isAdmin } = useAuth();
  const isMobile = useIsMobile();
  const [params] = useSearchParams();

  // Every one of these is a plain call made during render. Nothing memoises the role, so
  // an isAdmin flip in either direction is reflected on the very next render.
  const sections = visibleSections(isAdmin);
  const shown = resolveSection(wantedSection(params), isAdmin);
  // `resolveSection` only ever returns an id that is in `sections`, so the find always
  // hits — the `??` is a type guard, not a second failure mode to reason about.
  const section = sections.find(s => s.id === shown) ?? sections[0];
  const cards = visibleCards(section, isAdmin);

  return (
    <div style={pagePadding(isMobile)}>
      <h1 style={pageHeading(isMobile)}>Settings</h1>

      <div style={{ maxWidth: COLUMN_MAX_WIDTH, marginTop: 20 }}>
        <SettingsNav sections={sections} shown={shown} isMobile={isMobile} params={params} />

        {/* The page owns the gap between cards; SettingsCard owns what's inside one. */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
          {cards.map(card => {
            const Card = CARD_COMPONENTS[card.id];
            return <Card key={card.id} isMobile={isMobile} />;
          })}
        </div>
      </div>
    </div>
  );
}
