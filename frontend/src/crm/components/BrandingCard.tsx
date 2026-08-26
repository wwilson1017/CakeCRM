/**
 * BrandingCard — company name and logo (issue #9), lifted out of SettingsPage by #103.
 *
 * Consumes the already-built /api/branding backend. Saves push through BrandingContext so
 * the shell wordmark updates live. Mutations stay disabled until the branding fetch
 * resolves. Form fields fall back to the fetched branding until the user edits them (no
 * seeding effect). Logo mutations use patchBranding (functional) so an in-flight name save
 * can't be clobbered by an out-of-order logo response. The accent picker was removed in
 * #54 — the theme is fixed (light/dark), so branding is company name + logo only.
 */

import { useState } from 'react';

import { api } from '../../core/api/client';
import { useBranding } from '../../core/branding/BrandingContext';
import type { BrandingConfig } from '../../core/branding/brandingConfig';
import { toast } from '../../shared/toast';
import { IconX } from '../../shared/icons';
import { INK_MUTE, CORAL, FONT_SANS, labelStyle, inputStyle } from '../../shared/styles';
import { btnPrimary, btnSecondary, btnDanger } from '../styles';
import { BrandLogo } from './BrandLogo';
import { SettingsCard } from './SettingsCard';

// SVG is excluded: all logos are stored/served as image/png, and browsers don't
// content-sniff SVG, so an SVG would silently never render. (Serving real SVG from
// the unauthenticated logo endpoint would also be a stored-XSS surface.)
const ALLOWED_LOGO_TYPES = 'image/png,image/jpeg,image/gif,image/webp';
const MAX_LOGO_BYTES = 2 * 1024 * 1024; // 2 MB — mirrors the backend cap

// Visually hidden but still in the a11y tree and keyboard-focusable (unlike
// display:none), so the file input is reachable by Tab and operable by Enter.
const srOnly: React.CSSProperties = {
  position: 'absolute', width: 1, height: 1, padding: 0, margin: -1,
  overflow: 'hidden', clip: 'rect(0,0,0,0)', border: 0,
};

export function BrandingCard({ isMobile }: { isMobile: boolean }) {
  const { branding, loadError, patchBranding, logoVersion, bumpLogoVersion } = useBranding();
  const loaded = branding !== null;

  // Edits override the fetched value; until edited, fields mirror `branding`.
  const [nameEdit, setNameEdit] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [logoBusy, setLogoBusy] = useState(false);

  const nameVal = nameEdit ?? branding?.company_name ?? '';

  async function handleSave() {
    setSaving(true);
    try {
      const updated = await api<BrandingConfig>('/api/branding', {
        method: 'PUT',
        body: JSON.stringify({ company_name: nameVal.trim() }),
      });
      // Patch only the fields this save owns — never has_logo — so a slow save
      // can't clobber a logo uploaded/removed while it was in flight.
      patchBranding({ company_name: updated.company_name });
      toast.success('Branding saved.');
    } catch {
      toast.error('Failed to save branding.');
    } finally {
      setSaving(false);
    }
  }

  async function handleLogoUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ''; // allow re-selecting the same file after an error
    if (!file) return;
    if (file.size > MAX_LOGO_BYTES) {
      toast.error('Logo must be under 2 MB.');
      return;
    }
    setLogoBusy(true);
    try {
      const form = new FormData();
      form.append('file', file);
      await api('/api/branding/logo', { method: 'POST', body: form });
      patchBranding({ has_logo: true });
      bumpLogoVersion();
      toast.success('Logo updated.');
    } catch {
      toast.error('Failed to upload logo.');
    } finally {
      setLogoBusy(false);
    }
  }

  async function handleLogoRemove() {
    setLogoBusy(true);
    try {
      await api('/api/branding/logo', { method: 'DELETE' });
      patchBranding({ has_logo: false });
      bumpLogoVersion();
    } catch {
      toast.error('Failed to remove logo.');
    } finally {
      setLogoBusy(false);
    }
  }

  const fieldWrap: React.CSSProperties = { marginBottom: 20, maxWidth: 420 };

  return (
    <SettingsCard
      id="branding"
      title="Branding"
      description="Personalize how CakeCRM looks — your company name and logo appear throughout the app."
      isMobile={isMobile}
    >
      {loadError && (
        <p style={{
          fontFamily: FONT_SANS, fontSize: 13, color: CORAL, lineHeight: 1.5,
          margin: '0 0 20px',
        }}>
          Couldn't load your current branding. Reload the page before editing —
          saving now would overwrite it with defaults.
        </p>
      )}

      <div style={fieldWrap}>
        <label htmlFor="branding-company-name" style={labelStyle}>Company name</label>
        <input
          id="branding-company-name"
          style={inputStyle}
          value={nameVal}
          disabled={!loaded}
          placeholder="CakeCRM"
          onChange={e => setNameEdit(e.target.value)}
        />
      </div>

      <div style={fieldWrap}>
        <label style={labelStyle}>Logo</label>
        {branding?.has_logo && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}>
            <BrandLogo
              key={logoVersion}
              src={`/api/branding/logo?v=${logoVersion}`}
              alt="Current logo"
              style={{ height: 44, maxWidth: 160, objectFit: 'contain' }}
              fallback={<span style={{ fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE }}>Logo unavailable</span>}
            />
            <button
              onClick={handleLogoRemove}
              disabled={logoBusy}
              style={{ ...btnDanger, padding: '6px 12px', fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}
            ><IconX size={14} /> Remove</button>
          </div>
        )}
        <label style={{ ...btnSecondary, position: 'relative', display: 'inline-flex', cursor: loaded && !logoBusy ? 'pointer' : 'default', opacity: loaded && !logoBusy ? 1 : 0.6 }}>
          {logoBusy ? 'Uploading…' : branding?.has_logo ? 'Replace logo' : 'Upload logo'}
          <input
            type="file"
            accept={ALLOWED_LOGO_TYPES}
            disabled={!loaded || logoBusy}
            onChange={handleLogoUpload}
            style={srOnly}
          />
        </label>
        <p style={{ fontFamily: FONT_SANS, fontSize: 12, color: INK_MUTE, margin: '8px 0 0' }}>
          PNG, JPEG, GIF, or WebP — up to 2&nbsp;MB.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 12, marginTop: 28 }}>
        <button
          onClick={handleSave}
          disabled={!loaded || saving}
          style={{ ...btnPrimary, opacity: !loaded || saving ? 0.6 : 1, cursor: !loaded || saving ? 'wait' : 'pointer' }}
        >{saving ? 'Saving…' : 'Save branding'}</button>
      </div>
    </SettingsCard>
  );
}
