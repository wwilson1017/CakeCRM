/**
 * SettingsPage — CRM settings, currently the Branding section (issue #9).
 *
 * Consumes the already-built /api/branding backend: company name, accent color,
 * and logo. Saves push through BrandingContext so the shell (wordmark + accent)
 * restyles live. Mutations stay disabled until the branding fetch resolves.
 * Form fields fall back to the fetched branding until the user edits them (no
 * seeding effect). Logo mutations use patchBranding (functional) so an in-flight
 * name/accent save can't be clobbered by an out-of-order logo response. Ported in
 * shape from chatty's SettingsPanel Branding tab, restyled with CakeCRM tokens.
 */

import { useState } from 'react';
import { api } from '../core/api/client';
import { useBranding } from '../core/branding/BrandingContext';
import {
  DEFAULT_BRANDING, toSixDigitHex,
} from '../core/branding/brandingConfig';
import type { BrandingConfig } from '../core/branding/brandingConfig';
import { useIsMobile } from '../shared/useIsMobile';
import { toast } from '../shared/toast';
import { IconX } from '../shared/icons';
import {
  INK, INK_MUTE, LINE_STRONG, CORAL, FONT_SANS, FONT_MONO, labelStyle, inputStyle,
} from '../shared/styles';
import {
  pagePadding, pageHeading, sectionHeading, cardStyle, btnPrimary, btnSecondary, btnDanger,
} from './styles';
import { BrandLogo } from './components/BrandLogo';
import { GmailCard } from './components/GmailCard';

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

export function SettingsPage() {
  const isMobile = useIsMobile();
  const { branding, loadError, patchBranding, logoVersion, bumpLogoVersion } = useBranding();
  const loaded = branding !== null;

  // Edits override the fetched value; until edited, fields mirror `branding`.
  const [nameEdit, setNameEdit] = useState<string | null>(null);
  const [accentEdit, setAccentEdit] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [logoBusy, setLogoBusy] = useState(false);

  const nameVal = nameEdit ?? branding?.company_name ?? '';
  const accentVal = accentEdit ?? toSixDigitHex(branding?.accent_color ?? DEFAULT_BRANDING.accent_color);

  async function handleSave() {
    setSaving(true);
    try {
      const updated = await api<BrandingConfig>('/api/branding', {
        method: 'PUT',
        body: JSON.stringify({ company_name: nameVal.trim(), accent_color: accentVal }),
      });
      // Patch only the fields this save owns — never has_logo — so a slow save
      // can't clobber a logo uploaded/removed while it was in flight.
      patchBranding({ company_name: updated.company_name, accent_color: updated.accent_color });
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
    <div style={pagePadding(isMobile)}>
      <h1 style={pageHeading(isMobile)}>Settings</h1>

      <div style={{ ...cardStyle, padding: isMobile ? 20 : 28, marginTop: 24, maxWidth: 620 }}>
        <div style={sectionHeading()}>Branding</div>
        <p style={{
          fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE, lineHeight: 1.6,
          margin: '0 0 24px', maxWidth: 460,
        }}>
          Personalize how CakeCRM looks — your company name, accent color, and logo
          restyle the whole app.
        </p>

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
          <label htmlFor="branding-accent" style={labelStyle}>Accent color</label>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <input
              id="branding-accent"
              type="color"
              value={accentVal}
              disabled={!loaded}
              onChange={e => setAccentEdit(e.target.value)}
              style={{
                width: 44, height: 36, padding: 0, border: `1px solid ${LINE_STRONG}`,
                borderRadius: 4, background: 'transparent', cursor: loaded ? 'pointer' : 'default',
              }}
            />
            <span style={{ fontFamily: FONT_MONO, fontSize: 13, color: INK }}>{accentVal}</span>
          </div>
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
      </div>

      {/* Gmail connect (issue #8) — appended last in the settings card chain. */}
      <GmailCard isMobile={isMobile} />
    </div>
  );
}
