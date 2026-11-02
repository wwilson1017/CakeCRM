import { useEffect, useState } from 'react';
import { api } from '../../core/api/client';
import { toast } from '../../shared/toast';
import { useSetVoiceLivePreview } from '../../shared/voice/VoiceReadyContext';
import { SettingsCard } from './SettingsCard';

interface PreviewSettings { enabled: boolean; voice_ready: boolean; }
export function VoiceLiveCard({ isMobile }: { isMobile: boolean }) {
  const [settings, setSettings] = useState<PreviewSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const setLive = useSetVoiceLivePreview();
  useEffect(() => {
    let alive = true;
    api<PreviewSettings>('/api/voice/live-preview').then(value => {
      if (alive) setSettings(value);
    }).catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
  }, []);
  async function toggle(enabled: boolean) {
    if (busy || !settings) return;
    setBusy(true);
    try {
      const result = await api<{ enabled: boolean }>('/api/voice/live-preview', {
        method: 'POST', body: JSON.stringify({ enabled }),
      });
      setSettings({ ...settings, enabled: result.enabled });
      setLive(result.enabled);
    } catch { toast.error('Failed to update live transcription.'); }
    finally { setBusy(false); }
  }
  return <SettingsCard id="voice_live" title="Live transcription" isMobile={isMobile}
    description="Live transcription shows your words while you speak. To do that, your browser hands the microphone audio to its own speech recognition service, which is usually run online by the browser maker (for example Google for Chrome, Apple for Safari). That audio goes straight from the browser, not through this install, under that company's terms, and cancelling a recording cannot take back audio it has already heard. The text that is sent or saved still comes from the normal transcription after you let go. When the switch is off (the default), audio goes only through this install to your transcribing provider.">
    {/* An enabled switch stays visible after the transcriber goes away, so it can still be turned off. */}
    {settings?.voice_ready || settings?.enabled ? <label>
      <input type="checkbox" role="switch" checked={settings.enabled} disabled={busy}
        onChange={event => void toggle(event.target.checked)} /> Enable live transcription
    </label> : <p>{settings ? 'Needs an AI provider that can transcribe (today, an OpenAI key).'
      : failed ? 'Could not load live transcription settings. Reload to try again.' : 'Loading…'}</p>}
    <p>Other open tabs pick this up when reloaded.</p>
  </SettingsCard>;
}
