/**
 * CakeCRM — Branding context.
 *
 * Fetches the admin-global branding config once for the authenticated shell and
 * drives the theme's accent through the `--brand-color` / `--brand-color-soft`
 * CSS custom properties (see brandingConfig.ts).
 *
 * All DOM mutation lives in a provider-owned effect keyed on `branding`, so a
 * late save/upload that resolves AFTER the provider unmounts only touches React
 * state (ignored on an unmounted tree) and can never re-apply the accent to
 * /login or /setup. The vars are set on `document.documentElement` (not a
 * subtree) so App-level portals (ConfirmHost / ToastViewport) stay on-brand, and
 * removed on unmount so /login and /setup keep the CSS defaults. A failed initial
 * fetch falls back to DEFAULT_BRANDING so the shell (and Settings) stays usable.
 */

import { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { api } from '../api/client';
import { applyAccentVars, clearAccentVars, DEFAULT_BRANDING } from './brandingConfig';
import type { BrandingConfig } from './brandingConfig';

interface BrandingContextValue {
  /** null until the initial fetch resolves — consumers render defaults meanwhile. */
  branding: BrandingConfig | null;
  /**
   * Merge a partial config via a functional update — used for BOTH a settings save
   * ({company_name, accent_color}) and logo ops ({has_logo}). Each caller patches
   * only the fields it owns, so overlapping/out-of-order requests can't clobber each
   * other (a delayed save can't restore a stale has_logo, and vice versa).
   */
  patchBranding: (patch: Partial<BrandingConfig>) => void;
  /** Cache-buster bumped on logo upload/remove so <img src=".../logo?v="> refreshes. */
  logoVersion: number;
  bumpLogoVersion: () => void;
}

const BrandingCtx = createContext<BrandingContextValue | null>(null);

export function BrandingProvider({ children }: { children: ReactNode }) {
  const [branding, setBranding] = useState<BrandingConfig | null>(null);
  // Seed from a timestamp so the cache-buster never resets to a value a prior
  // logo response was cached under (a plain 0 would re-serve a stale logo after
  // a remount/reload following a logo replace).
  const [logoVersion, setLogoVersion] = useState(() => Date.now());

  // Fetch once; state-only. Fall back to defaults on failure so the shell stays
  // fully usable (Settings editable, default accent) rather than blank/disabled.
  useEffect(() => {
    let cancelled = false;
    api<BrandingConfig>('/api/branding')
      .then(b => { if (!cancelled) setBranding(b); })
      .catch(() => { if (!cancelled) setBranding(DEFAULT_BRANDING); });
    return () => { cancelled = true; };
  }, []);

  // Apply the accent whenever branding changes; remove it only on unmount.
  useEffect(() => {
    if (branding) applyAccentVars(branding.accent_color);
  }, [branding]);
  useEffect(() => () => clearAccentVars(), []);

  const value: BrandingContextValue = {
    branding,
    patchBranding: patch => setBranding(prev => ({ ...(prev ?? DEFAULT_BRANDING), ...patch })),
    logoVersion,
    bumpLogoVersion: () => setLogoVersion(v => v + 1),
  };
  return <BrandingCtx.Provider value={value}>{children}</BrandingCtx.Provider>;
}

export function useBranding(): BrandingContextValue {
  const ctx = useContext(BrandingCtx);
  if (!ctx) throw new Error('useBranding must be used within a BrandingProvider');
  return ctx;
}
