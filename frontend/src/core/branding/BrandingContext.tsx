/**
 * CakeCRM — Branding context.
 *
 * Fetches the admin-global branding config once for the authenticated shell and
 * drives the theme's accent through the `--brand-color` / `--brand-color-soft`
 * CSS custom properties (see brandingConfig.ts). Vars are set on
 * `document.documentElement` (not a subtree) so App-level portals (ConfirmHost /
 * ToastViewport) stay on-brand. The provider wraps the /crm subtree only, so
 * /login and /setup keep the CSS defaults — the vars are removed on unmount, and
 * a late fetch is ignored after unmount (StrictMode-safe).
 */

import { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { api } from '../api/client';
import { applyAccentVars, clearAccentVars } from './brandingConfig';
import type { BrandingConfig } from './brandingConfig';

interface BrandingContextValue {
  /** null until the initial fetch resolves — consumers render defaults meanwhile. */
  branding: BrandingConfig | null;
  /** Push a config (e.g. a PUT/logo result) into the shell and re-apply the accent live. */
  applyBranding: (b: BrandingConfig) => void;
  /** Cache-buster bumped on logo upload/remove so <img src=".../logo?v="> refreshes. */
  logoVersion: number;
  bumpLogoVersion: () => void;
}

const BrandingCtx = createContext<BrandingContextValue | null>(null);

export function BrandingProvider({ children }: { children: ReactNode }) {
  const [branding, setBranding] = useState<BrandingConfig | null>(null);
  const [logoVersion, setLogoVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api<BrandingConfig>('/api/branding')
      .then(b => {
        if (cancelled) return;
        setBranding(b);
        applyAccentVars(b.accent_color);
      })
      .catch(() => { /* best-effort: CSS defaults stay in effect */ });
    return () => {
      // Guard the async race (a late fetch must not re-apply after unmount /
      // StrictMode remount) and restore the CSS defaults for /login and /setup.
      cancelled = true;
      clearAccentVars();
    };
  }, []);

  const value: BrandingContextValue = {
    branding,
    applyBranding: (b: BrandingConfig) => {
      setBranding(b);
      applyAccentVars(b.accent_color);
    },
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
