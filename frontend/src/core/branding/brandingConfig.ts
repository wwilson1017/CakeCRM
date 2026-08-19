/**
 * CakeCRM — Branding config types (no React).
 *
 * Branding is company name + logo only. The theme itself is FIXED — one polished
 * CakeCRM look in light and dark, defined as `--color-ck-*` tokens in index.css
 * (issue #54 retired the user-configurable accent and its CSS-variable routing).
 */

export interface BrandingConfig {
  company_name: string;
  has_logo: boolean;
}

export const DEFAULT_BRANDING: BrandingConfig = {
  company_name: 'CakeCRM',
  has_logo: false,
};
