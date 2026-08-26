/**
 * SettingsCard — the one card shell every Settings card wears (#103).
 *
 * Before this, six cards spread `{...cardStyle, padding, marginTop, maxWidth}` and three
 * rendered bare `cardStyle` — so those three had no padding at all and butted against the
 * card above them. One shell ends both dialects.
 *
 * Two invariants, and the second is the one that bites:
 *
 *  1. This component is the ONLY place a Settings card's padding is defined. The PAGE owns
 *     column width and inter-card spacing, which is why there is no `marginTop` and no
 *     `maxWidth` here.
 *  2. A card REPLACES its outer wrapper with this — it never nests its old `<div>` inside.
 *     Wrapping instead of replacing is what would double the padding on the six and draw a
 *     border inside a border. `settingsSections.test.ts` fails the build if any settings
 *     card still imports `cardStyle`.
 *
 * The `<h2>` carries the same mono/uppercase `sectionHeading()` look the cards had as a
 * `<div>`, but as a real heading — the page is h1 → h2 (card) → h3 (in-card subsection),
 * so screen-reader outline navigation actually works.
 */

import type { ReactNode } from 'react';

import { cardStyle, sectionHeading, settingsDescription } from '../styles';
import type { SettingsCardId } from '../settingsSections';

export function SettingsCard({ id, title, description, badge, isMobile, children }: {
  id: SettingsCardId;
  title: string;
  description?: ReactNode;
  /** Inline status chip rendered beside the title (Gmail's "• connected"). */
  badge?: ReactNode;
  isMobile: boolean;
  children: ReactNode;
}) {
  const titleId = `${id}-title`;
  return (
    <section
      id={id}
      aria-labelledby={titleId}
      style={{ ...cardStyle, padding: isMobile ? 20 : 28 }}
    >
      <h2 id={titleId} style={{ ...sectionHeading(), display: 'flex', alignItems: 'center', gap: 8 }}>
        {title}
        {badge}
      </h2>
      {description !== undefined && <p style={settingsDescription}>{description}</p>}
      {children}
    </section>
  );
}
