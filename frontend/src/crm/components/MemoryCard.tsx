/**
 * MemoryCard — the Settings entry point to the Memory page (issue #72).
 *
 * A card rather than a nav tab: the CRM's top-level nav is for business objects, and
 * every other assistant-facing surface (identity, Telegram, Gmail, notifications) is
 * already a Settings card.
 */

import { Link } from 'react-router-dom';
import { INK_MUTE } from '../../shared/styles';
import { cardStyle, sectionHeading, btnSecondary } from '../styles';

export function MemoryCard() {
  return (
    <div style={cardStyle}>
      <div style={sectionHeading()}>Assistant memory</div>
      <p style={{ color: INK_MUTE, marginTop: 0, maxWidth: 560 }}>
        Read and edit what your assistant knows: its own description of itself, its
        running snapshot, topic notes, daily logs, and the individual facts it has
        recorded. Works with or without an AI provider configured.
      </p>
      <Link to="/crm/memory" style={{ ...btnSecondary, display: 'inline-block', textDecoration: 'none' }}>
        Open memory
      </Link>
    </div>
  );
}
