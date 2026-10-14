/**
 * Export data (#302, ported from PlaybackCRM #34): every table as CSV, zipped. Admin-only
 * (the card is gated in settingsSections.ts and the route is `require_admin`).
 *
 * Not a plain `<a download>`: the route needs the Bearer token, which a browser-issued
 * link request does not carry, so the bytes come through `apiResponse` and an object URL.
 * The file is named from the server's `Content-Disposition`, which stamps the install's
 * local day; the browser's own clock could be on another calendar day. The object URL is
 * revoked once the click has been handed to the browser.
 */

import { useState } from 'react';

import { apiResponse, attachmentName } from '../../core/api/client';
import { toast } from '../../shared/toast';
import { btnPrimary } from '../styles';
import { SettingsCard } from './SettingsCard';

const REVOKE_DELAY_MS = 60_000;

export function ExportCard({ isMobile }: { isMobile: boolean }) {
  const [busy, setBusy] = useState(false);

  async function download() {
    setBusy(true);
    try {
      const res = await apiResponse('/api/crm/export');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = attachmentName(res.headers.get('Content-Disposition'), 'cakecrm-export.zip');
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS);
    } catch {
      toast.error('Failed to export data.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <SettingsCard
      id="export"
      title="Export data"
      description="Everything in the CRM as CSV files in one ZIP: companies, contacts, deals, activity, todos, notes and custom fields. Opens in any spreadsheet. Passwords and keys are never included."
      isMobile={isMobile}
    >
      <button type="button" onClick={() => void download()} disabled={busy} style={btnPrimary}>
        {busy ? 'Preparing…' : 'Download CSV export'}
      </button>
    </SettingsCard>
  );
}
