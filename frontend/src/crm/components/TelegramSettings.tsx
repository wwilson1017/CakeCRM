/**
 * TelegramSettings — connect a Telegram bot and link your phone to the assistant (#7).
 *
 * Self-contained settings card (rendered by SettingsPage after Branding). Mirrors the
 * repo's load→edit→save + `null = unknown` idiom: status is null until the first fetch
 * resolves, so nothing renders assumptively. While connected-but-unlinked it polls
 * status every few seconds so the card notices when `/start <code>` links the account.
 * The bot token is a password field, cleared immediately after submit, and never echoed
 * back by the API. Assistant replies need an AI provider, so a soft note shows when
 * `ai_ready` is false — the connection itself still works without a key.
 */

import { useEffect, useRef, useState } from 'react';

import { api } from '../../core/api/client';
import { FONT_SANS, INK, INK_MUTE, ACCENT, labelStyle, inputStyle } from '../../shared/styles';
import { toast } from '../../shared/toast';
import { cardStyle, sectionHeading, btnPrimary, btnSecondary, btnDanger } from '../styles';

interface TelegramStatus {
  connected: boolean;
  bot_username: string;
  linked: boolean;
  linked_name: string;
  link_code: string;
  link_url: string;
}

interface SetupStatus {
  ai_ready: boolean;
}

export function TelegramSettings() {
  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [aiReady, setAiReady] = useState<boolean | null>(null);
  const [tokenInput, setTokenInput] = useState('');
  const [busy, setBusy] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = () => api<TelegramStatus>('/api/telegram/status').then(setStatus).catch(() => { /* keep unknown */ });

  useEffect(() => {
    refresh();
    api<SetupStatus>('/api/setup/status').then((s) => setAiReady(s.ai_ready)).catch(() => setAiReady(null));
  }, []);

  // Poll only while we're waiting for the user to link from Telegram, so the card
  // flips to "linked" on its own. Stop as soon as that's no longer the state.
  useEffect(() => {
    const waiting = status?.connected && !status?.linked;
    if (waiting && pollRef.current === null) {
      pollRef.current = setInterval(refresh, 4000);
    }
    if (!waiting && pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => {
      if (pollRef.current !== null) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [status?.connected, status?.linked]);

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

  async function regenerate() {
    setBusy(true);
    try {
      setStatus(await api<TelegramStatus>('/api/telegram/link-code/regenerate', { method: 'POST' }));
    } catch {
      toast.error('Failed to regenerate the link code.');
    } finally {
      setBusy(false);
    }
  }

  const loaded = status !== null;
  const desc: React.CSSProperties = { fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE, lineHeight: 1.6, margin: '0 0 20px', maxWidth: 460 };
  const fieldWrap: React.CSSProperties = { marginBottom: 20, maxWidth: 420 };

  return (
    <div style={{ ...cardStyle, padding: 28, marginTop: 24, maxWidth: 620 }}>
      <div style={sectionHeading()}>Telegram</div>
      <p style={desc}>
        Chat with your CakeCRM assistant from Telegram — ask about contacts, deals, and
        tasks, and approve any changes right from your phone.
      </p>

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
            <a href="https://t.me/BotFather" target="_blank" rel="noreferrer" style={{ color: ACCENT }}>@BotFather</a>{' '}
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

      {loaded && status.connected && !status.linked && (
        <div style={fieldWrap}>
          <p style={{ fontFamily: FONT_SANS, fontSize: 13.5, color: INK, margin: '0 0 12px' }}>
            Bot <strong>@{status.bot_username}</strong> is connected. Link this device to start chatting:
          </p>
          {status.link_url && (
            <a href={status.link_url} target="_blank" rel="noreferrer"
               style={{ ...btnPrimary, display: 'inline-block', textDecoration: 'none', marginBottom: 12 }}>
              Open Telegram to link
            </a>
          )}
          <p style={{ fontFamily: FONT_SANS, fontSize: 12.5, color: INK_MUTE, lineHeight: 1.6, margin: '4px 0 16px' }}>
            Or, in your Telegram chat with the bot, send{' '}
            <code style={{ color: INK }}>/link {status.link_code}</code>. The code links the
            first device that uses it, then expires.
          </p>
          <div style={{ display: 'flex', gap: 12 }}>
            <button onClick={regenerate} disabled={busy} style={{ ...btnSecondary, opacity: busy ? 0.6 : 1 }}>
              Regenerate code
            </button>
            <button onClick={disconnect} disabled={busy} style={{ ...btnDanger, opacity: busy ? 0.6 : 1 }}>
              Disconnect
            </button>
          </div>
        </div>
      )}

      {loaded && status.connected && status.linked && (
        <div style={fieldWrap}>
          <p style={{ fontFamily: FONT_SANS, fontSize: 13.5, color: INK, margin: '0 0 4px' }}>
            ✅ Linked{status.linked_name ? ` to ${status.linked_name}` : ''} via{' '}
            <strong>@{status.bot_username}</strong>.
          </p>
          <p style={{ fontFamily: FONT_SANS, fontSize: 12.5, color: INK_MUTE, margin: '0 0 16px' }}>
            You can now message the assistant from Telegram.
          </p>
          <button onClick={disconnect} disabled={busy} style={{ ...btnDanger, opacity: busy ? 0.6 : 1 }}>
            Disconnect
          </button>
        </div>
      )}
    </div>
  );
}
