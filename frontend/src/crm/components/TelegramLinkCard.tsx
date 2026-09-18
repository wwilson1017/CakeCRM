/**
 * TelegramLinkCard — link MY phone to the assistant (#193, multi-user Phase B / B4).
 *
 * The personal half of what used to be one admin-only Telegram card. The bot itself is
 * install configuration and stays in `TelegramSettings` (Integrations, admin); the chat
 * binding is a person's own device, so it lives in the Personal section and is visible to
 * every seat.
 *
 * There is no redaction here and none is needed: since #193 a link code claims the
 * caller's OWN `telegram_links` row, so showing it to a member gives them access to
 * nothing but their own assistant. (Before #193 one code claimed the single install-wide
 * binding, which is why the old card hid it from members.)
 *
 * `null = unknown` per the repo idiom: status is null until the first fetch resolves, so
 * nothing renders assumptively. While connected-but-unlinked with a code minted, it polls
 * status every few seconds so the card notices when `/start <code>` lands.
 */

import { useEffect, useRef, useState } from 'react';

import { api } from '../../core/api/client';
import { FONT_SANS, INK, INK_MUTE, labelStyle } from '../../shared/styles';
import { toast } from '../../shared/toast';
import { btnPrimary, btnSecondary, btnDanger } from '../styles';
import { SettingsCard } from './SettingsCard';

interface TelegramStatus {
  connected: boolean;
  bot_username: string;
  linked: boolean;
  linked_name: string;
  link_code: string;
  link_url: string;
}

export function TelegramLinkCard({ isMobile }: { isMobile: boolean }) {
  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = () =>
    api<TelegramStatus>('/api/telegram/status').then(setStatus).catch(() => { /* keep unknown */ });

  useEffect(() => { refresh(); }, []);

  // Poll only while a code is outstanding and waiting to be used, so the card flips to
  // "linked" on its own. Stop as soon as that's no longer the state.
  useEffect(() => {
    const waiting = status?.connected && !status?.linked && !!status?.link_code;
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
  }, [status?.connected, status?.linked, status?.link_code]);

  async function mintCode() {
    setBusy(true);
    try {
      setStatus(await api<TelegramStatus>('/api/telegram/link-code', { method: 'POST' }));
    } catch {
      toast.error('Failed to create a link code.');
    } finally {
      setBusy(false);
    }
  }

  async function unlink() {
    setBusy(true);
    try {
      setStatus(await api<TelegramStatus>('/api/telegram/unlink', { method: 'POST' }));
      toast.success('Telegram unlinked.');
    } catch {
      toast.error('Failed to unlink.');
    } finally {
      setBusy(false);
    }
  }

  const loaded = status !== null;
  const fieldWrap: React.CSSProperties = { marginBottom: 20, maxWidth: 420 };

  return (
    <SettingsCard
      id="telegram_link"
      title="Link my Telegram"
      description="Chat with your CakeCRM assistant from your own phone — ask about contacts, deals, and tasks, and approve any changes right from Telegram."
      isMobile={isMobile}
    >
      {!loaded && (
        <p style={{ fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE }}>Loading…</p>
      )}

      {loaded && !status.connected && (
        <p style={{ fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE, lineHeight: 1.6, margin: 0 }}>
          No Telegram bot is connected for this workspace yet. An admin can add one in
          Settings → Integrations → Telegram, and then you can link your phone here.
        </p>
      )}

      {loaded && status.connected && !status.linked && (
        <div style={fieldWrap}>
          {!status.link_code && (
            <>
              <p style={{ fontFamily: FONT_SANS, fontSize: 13.5, color: INK, margin: '0 0 12px' }}>
                Bot <strong>@{status.bot_username}</strong> is ready. Create your personal
                link code to connect this account to Telegram.
              </p>
              <button
                onClick={mintCode}
                disabled={busy}
                style={{ ...btnPrimary, opacity: busy ? 0.6 : 1 }}
              >{busy ? 'Creating…' : 'Get my link code'}</button>
            </>
          )}

          {status.link_code && (
            <>
              <p style={{ fontFamily: FONT_SANS, fontSize: 13.5, color: INK, margin: '0 0 12px' }}>
                Link this device to start chatting:
              </p>
              {status.link_url && (
                <a href={status.link_url} target="_blank" rel="noreferrer"
                   style={{ ...btnPrimary, display: 'inline-block', textDecoration: 'none', marginBottom: 12 }}>
                  Open Telegram to link
                </a>
              )}
              <div style={{ margin: '4px 0 16px' }}>
                <span style={labelStyle}>Your link code</span>
                <p style={{ fontFamily: FONT_SANS, fontSize: 12.5, color: INK_MUTE, lineHeight: 1.6, margin: '6px 0 0' }}>
                  Or, in your Telegram chat with the bot, send{' '}
                  <code style={{ color: INK }}>/link {status.link_code}</code>. The code
                  links the first device that uses it, then expires. It is yours alone —
                  it connects Telegram to your account, not to anybody else's.
                </p>
              </div>
              <button onClick={mintCode} disabled={busy} style={{ ...btnSecondary, opacity: busy ? 0.6 : 1 }}>
                Regenerate code
              </button>
            </>
          )}
        </div>
      )}

      {loaded && status.connected && status.linked && (
        <div style={fieldWrap}>
          <p style={{ fontFamily: FONT_SANS, fontSize: 13.5, color: INK, margin: '0 0 4px' }}>
            ✅ Linked{status.linked_name ? ` as ${status.linked_name}` : ''} via{' '}
            <strong>@{status.bot_username}</strong>.
          </p>
          <p style={{ fontFamily: FONT_SANS, fontSize: 12.5, color: INK_MUTE, margin: '0 0 16px' }}>
            You can now message the assistant from Telegram, and anything it changes is
            recorded as you.
          </p>
          <button onClick={unlink} disabled={busy} style={{ ...btnDanger, opacity: busy ? 0.6 : 1 }}>
            Unlink my Telegram
          </button>
        </div>
      )}
    </SettingsCard>
  );
}
