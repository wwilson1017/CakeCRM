/**
 * TelegramSettings — connect the workspace's Telegram bot (#7; split in #193).
 *
 * Install configuration only: one bot token for the whole workspace, one poller, admin
 * gated. Linking a phone is personal and moved to `TelegramLinkCard` in the Personal
 * section when #193 gave every seat its own link — this card no longer shows a link code,
 * because there is no longer one install-wide binding for a code to claim.
 *
 * Rendered in the Integrations section of Settings. Mirrors the repo's load→edit→save +
 * `null = unknown` idiom: status is null until the first fetch resolves, so nothing
 * renders assumptively. The bot token is a password field, cleared immediately after
 * submit, and never echoed back by the API. Assistant replies need an AI provider, so a
 * soft note shows when `ai_ready` is false — the connection itself still works without a
 * key.
 */

import { useEffect, useState } from 'react';

import { api } from '../../core/api/client';
import { FONT_SANS, INK, INK_MUTE, ACCENT_TEXT, labelStyle, inputStyle } from '../../shared/styles';
import { toast } from '../../shared/toast';
import { btnPrimary, btnDanger } from '../styles';
import { SettingsCard } from './SettingsCard';

// Only the install-wide half of /api/telegram/status. The response also carries the
// caller's own link fields; `TelegramLinkCard` is what reads those.
interface TelegramStatus {
  connected: boolean;
  bot_username: string;
}

interface SetupStatus {
  ai_ready: boolean;
}

export function TelegramSettings({ isMobile }: { isMobile: boolean }) {
  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [aiReady, setAiReady] = useState<boolean | null>(null);
  const [tokenInput, setTokenInput] = useState('');
  const [busy, setBusy] = useState(false);

  const refresh = () => api<TelegramStatus>('/api/telegram/status').then(setStatus).catch(() => { /* keep unknown */ });

  useEffect(() => {
    refresh();
    api<SetupStatus>('/api/setup/status').then((s) => setAiReady(s.ai_ready)).catch(() => setAiReady(null));
  }, []);

  async function connect() {
    const token = tokenInput.trim();
    if (!token) return;
    setBusy(true);
    try {
      const next = await api<TelegramStatus>('/api/telegram/connect', {
        method: 'POST',
        body: JSON.stringify({ bot_token: token }),
      });
      setTokenInput('');  // never keep the token in component state
      setStatus(next);
      toast.success('Telegram bot connected.');
    } catch (e) {
      toast.error(e instanceof Error ? e.message.replace(/^API error \d+: /, '') : 'Failed to connect the bot.');
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    try {
      setStatus(await api<TelegramStatus>('/api/telegram/disconnect', { method: 'POST' }));
      toast.success('Telegram disconnected.');
    } catch {
      toast.error('Failed to disconnect.');
    } finally {
      setBusy(false);
    }
  }

  const loaded = status !== null;
  const fieldWrap: React.CSSProperties = { marginBottom: 20, maxWidth: 420 };

  return (
    <SettingsCard
      id="telegram"
      title="Telegram"
      description="Connect a Telegram bot for the workspace. Once it's connected, everyone can link their own phone to the assistant from their Personal settings."
      isMobile={isMobile}
    >
      {aiReady === false && (
        <p style={{ fontFamily: FONT_SANS, fontSize: 12.5, color: INK_MUTE, lineHeight: 1.5, margin: '0 0 18px' }}>
          Add an AI provider in Settings to enable assistant replies — you can connect the
          bot now either way.
        </p>
      )}

      {!loaded && (
        <p style={{ fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE }}>Loading…</p>
      )}

      {loaded && !status.connected && (
        <div style={fieldWrap}>
          <label htmlFor="tg-token" style={labelStyle}>Bot token</label>
          <input
            id="tg-token"
            type="password"
            autoComplete="off"
            style={inputStyle}
            value={tokenInput}
            placeholder="123456:ABC-DEF…"
            disabled={busy}
            onChange={(e) => setTokenInput(e.target.value)}
          />
          <p style={{ fontFamily: FONT_SANS, fontSize: 12, color: INK_MUTE, margin: '8px 0 0' }}>
            Create a bot with{' '}
            <a href="https://t.me/BotFather" target="_blank" rel="noreferrer" style={{ color: ACCENT_TEXT }}>@BotFather</a>{' '}
            and paste the token it gives you.
          </p>
          <div style={{ marginTop: 16 }}>
            <button
              onClick={connect}
              disabled={busy || !tokenInput.trim()}
              style={{ ...btnPrimary, opacity: busy || !tokenInput.trim() ? 0.6 : 1 }}
            >{busy ? 'Connecting…' : 'Connect bot'}</button>
          </div>
        </div>
      )}

      {loaded && status.connected && (
        <div style={fieldWrap}>
          <p style={{ fontFamily: FONT_SANS, fontSize: 13.5, color: INK, margin: '0 0 4px' }}>
            ✅ Bot <strong>@{status.bot_username}</strong> is connected.
          </p>
          <p style={{ fontFamily: FONT_SANS, fontSize: 12.5, color: INK_MUTE, lineHeight: 1.6, margin: '0 0 16px' }}>
            Everyone on the team can now link their own phone from Settings → Personal →
            Link my Telegram. Disconnecting the bot unlinks every device.
          </p>
          <button onClick={disconnect} disabled={busy} style={{ ...btnDanger, opacity: busy ? 0.6 : 1 }}>
            Disconnect
          </button>
        </div>
      )}
    </SettingsCard>
  );
}
