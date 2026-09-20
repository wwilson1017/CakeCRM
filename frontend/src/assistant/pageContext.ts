// Which settings section the assistant should be told about (issue #200).
//
// Pure rules in their own module, the way `crm/dealDeepLink.ts` and `crm/pipelineFilters.ts`
// hold theirs — so the derivation is unit-testable with no DOM and the launcher stays a
// one-liner.
//
// It is DERIVED FROM THE URL rather than published by `SettingsPage` through a second
// RecordContext, because the section that page shows is already a pure function of the URL
// and the role, computed by exactly the two helpers called below. Reusing them makes
// disagreement between what the user sees and what the assistant is told impossible, with
// no provider, no publisher and no ownership-token dance — and `resolveSection` hands us
// the member fallback for free: a member deep-linked to an admin section lands on the
// default, and that is the section reported.

import { resolveSection, wantedSection } from '../crm/settingsSections';
import type { SettingsPageContext } from './types';

/** The Settings route. The only page with a context of this shape today. */
export const SETTINGS_PATH = '/crm/settings';

export function settingsPageContext(
  pathname: string,
  params: URLSearchParams,
  isAdmin: boolean,
): SettingsPageContext | null {
  if (pathname !== SETTINGS_PATH) return null;
  return { page: 'settings', section: resolveSection(wantedSection(params), isAdmin) };
}
