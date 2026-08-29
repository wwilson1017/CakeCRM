/**
 * MemoryCard — the Settings entry point to the Memory page (issue #72).
 *
 * A card rather than a nav tab: the CRM's top-level nav is for business objects, and
 * every other assistant-facing surface (identity, Telegram, Gmail, notifications) is
 * already a Settings card.
 */

import { Link } from 'react-router-dom';
import { btnSecondary } from '../styles';
import { SettingsCard } from './SettingsCard';

export function MemoryCard({ isMobile }: { isMobile: boolean }) {
  return (
    <SettingsCard
      id="memory"
      title="Assistant memory"
      description="Read and edit what your assistant knows: its own description of itself, its running snapshot, topic notes, daily logs, and the individual facts it has recorded. Works with or without an AI provider configured."
      isMobile={isMobile}
    >
      <Link to="/crm/memory" style={{ ...btnSecondary, display: 'inline-block', textDecoration: 'none' }}>
        Open memory
      </Link>
    </SettingsCard>
  );
}
