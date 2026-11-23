import { useEffect, useState } from 'react';

import { api, apiResponse, attachmentName } from '../../core/api/client';
import { toast } from '../../shared/toast';
import { INK_MUTE, inputStyle, labelStyle } from '../../shared/styles';
import { parseUTC } from '../gtd/util';
import { btnPrimary, btnSecondary } from '../styles';
import { SettingsCard } from './SettingsCard';

interface BackupStatus {
  configured: boolean;
  endpoint: string | null;
  bucket: string | null;
  region: string;
  prefix: string;
  access_key_id: string | null;
  has_secret: boolean;
  has_passphrase: boolean;
  retention: number;
  last_success_at: string | null;
  last_success_bytes: number | null;
  last_error: string | null;
  stale: boolean;
}

const emptyForm = { endpoint: '', bucket: '', region: 'us-east-1', prefix: '', access_key_id: '', secret_key: '', retention: 30 };
const fields = [
  ['endpoint', 'S3 endpoint'], ['bucket', 'Bucket'], ['region', 'Region'],
  ['prefix', 'Path prefix (optional)'], ['access_key_id', 'Access key ID'], ['secret_key', 'Secret key'],
] as const;
const backupInput = { ...inputStyle, textTransform: 'none', letterSpacing: 'normal' } as const;
const actions = { display: 'flex', flexWrap: 'wrap', gap: 8 } as const;
const para = { margin: '8px 0' } as const;
const heading = { margin: '24px 0 8px', fontSize: 15 } as const;

export function BackupsCard({ isMobile }: { isMobile: boolean }) {
  const [status, setStatus] = useState<BackupStatus | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [phrase, setPhrase] = useState('');
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState(false);

  function apply(value: BackupStatus) {
    setStatus(value);
    setForm({ endpoint: value.endpoint ?? '', bucket: value.bucket ?? '', region: value.region,
      prefix: value.prefix, access_key_id: value.access_key_id ?? '', secret_key: '', retention: value.retention });
  }

  useEffect(() => {
    let active = true;
    api<BackupStatus>('/api/backups/status').then(value => { if (active) apply(value); })
      .catch(() => { if (active) setLoadError(true); });
    return () => { active = false; };
  }, []);

  async function perform(work: () => Promise<void>) {
    setBusy(true);
    try { await work(); }
    catch (error) { toast.error(error instanceof Error ? error.message.replace(/^API error \d+: /, '') : 'Backup action failed.'); }
    finally { setBusy(false); }
  }

  async function update(path: string, method: string, body?: unknown) {
    apply(await api<BackupStatus>(`/api/backups/${path}`, { method, body: body === undefined ? undefined : JSON.stringify(body) }));
    toast.success('Backup settings saved.');
  }

  async function download() {
    const res = await apiResponse('/api/backups/download');
    // simplification: authenticated blob download, like ExportCard; suited to a few hundred MB.
    // A short-lived signed download link would remove browser buffering for larger installs.
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement('a');
    a.href = url;
    a.download = attachmentName(res.headers.get('Content-Disposition'), 'cakecrm-backup.dump');
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }

  return <SettingsCard id="backups" title="Backups" isMobile={isMobile}
    description="Download a restorable copy of the full database, including attachments and account data, or copy it daily to your own bucket.">
    {loadError && <p role="alert" style={para}>Could not load backup settings. Reload this page to try again.</p>}
    {!status && !loadError && <p style={para}>Loading backups…</p>}
    {status && <>
      <button type="button" style={btnPrimary} disabled={busy} onClick={() => void perform(download)}>
        {busy ? 'Working…' : 'Download backup now'}
      </button>
      <p style={{ ...para, color: INK_MUTE }}>Downloads are {status.has_passphrase ? 'encrypted (.dump.age)' : 'plain PostgreSQL dumps (.dump)'}.</p>
      {status.stale && <p role="alert" style={para}>No successful off-site backup in more than two days. Check the destination and test the connection.</p>}
      <p style={para}>Last successful upload: {status.last_success_at
        ? `${parseUTC(status.last_success_at).toLocaleString()} · ${((status.last_success_bytes ?? 0) / 1024 / 1024).toFixed(1)} MB`
        : 'None yet'}</p>
      {status.last_error && <p role="alert" style={para}>Last error: {status.last_error}</p>}
      <form onSubmit={e => { e.preventDefault(); void perform(() => update('destination', 'PUT', form)); }}>
        <h3 style={heading}>Daily off-site copy</h3>
        <p style={para}>Use a bucket or prefix dedicated to this install. Older CakeCRM backups there are removed according to retention. Saving starts daily backups; failures retry hourly.</p>
        <p style={{ ...para, color: INK_MUTE }}>Use your provider’s region (default us-east-1; for R2 use auto). Test connection uses the saved settings.</p>
        {fields.map(([key, label]) => <label key={key} style={{ ...labelStyle, display: 'block', marginBottom: 10 }}>
          {label}
          <input style={backupInput}
            type={key === 'secret_key' ? 'password' : key === 'endpoint' ? 'url' : 'text'}
            autoComplete="off" value={form[key]} disabled={busy}
            required={key !== 'prefix' && (key !== 'secret_key' || !status.has_secret)}
            placeholder={key === 'secret_key' && status.has_secret ? 'Saved — leave blank to keep' : undefined}
            onChange={e => setForm({ ...form, [key]: e.target.value })} />
        </label>)}
        <label style={{ ...labelStyle, display: 'block', marginBottom: 10 }}>Backups to retain
          <input style={backupInput} type="number" min={1} max={365} required value={form.retention} disabled={busy}
            onChange={e => setForm({ ...form, retention: Number(e.target.value) })} />
        </label>
        <div style={actions}>
          <button type="submit" style={btnPrimary} disabled={busy}>Save destination</button>
          <button type="button" style={btnSecondary} disabled={busy || !status.configured} onClick={() => void perform(async () => {
            await api('/api/backups/test', { method: 'POST' }); toast.success('Connection tested: wrote, listed and removed an object.');
          })}>Test connection</button>
          <button type="button" style={btnSecondary} disabled={busy || !status.configured}
            onClick={() => void perform(() => update('destination', 'DELETE'))}>Remove destination</button>
        </div>
      </form>
      <form onSubmit={e => { e.preventDefault(); void perform(async () => {
        await update('passphrase', 'PUT', { passphrase: phrase }); setPhrase('');
      }); }}>
        <h3 style={heading}>Optional encryption</h3>
        <p style={para}>If you lose this passphrase, your backups cannot be opened. CakeCRM cannot recover it.</p>
        <p style={para}>Without a passphrase, backups still work. Changing or removing it affects future backups only. Keep old passphrases for old files.</p>
        <label style={labelStyle}>Backup passphrase
          <input type="password" autoComplete="new-password" minLength={8} maxLength={1024} required
            style={{ ...backupInput, marginBottom: 10 }}
            value={phrase} disabled={busy} placeholder={status.has_passphrase ? 'A passphrase is saved' : 'At least 8 characters'}
            onChange={e => setPhrase(e.target.value)} />
        </label>
        <div style={actions}>
          <button type="submit" style={btnPrimary} disabled={busy}>Set passphrase</button>
          <button type="button" style={btnSecondary} disabled={busy || !status.has_passphrase}
            onClick={() => void perform(async () => { await update('passphrase', 'PUT', { passphrase: null }); setPhrase(''); })}>Remove passphrase</button>
        </div>
      </form>
      <p style={{ ...para, color: INK_MUTE }}>Keep your install’s ENCRYPTION_KEY and branding logo separately. See the manual for restore steps.</p>
    </>}
  </SettingsCard>;
}
