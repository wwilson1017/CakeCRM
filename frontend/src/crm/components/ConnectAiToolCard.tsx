/**
 * ConnectAiToolCard — connect MY own AI tool to CakeCRM (#322).
 *
 * Two ways in, one list. ChatGPT and Claude sign in with OAuth (they open CakeCRM's own
 * sign-in page), which needs this install to have a public https address. Coding tools
 * (Claude Code, Codex, Gemini CLI, Cursor, VS Code) take a personal token pasted into
 * their config. Both appear in the same list, each with its call log and Revoke.
 *
 * Member-visible (Personal): every route it drives acts on the caller's own seat. When an
 * admin has not let members connect, it says so and shows no control that could 403.
 * Never keyed off AI provider setup: the connected tool brings its own AI.
 */

import { useEffect, useState } from 'react';

import { api, ApiError } from '../../core/api/client';
import { copyToClipboard } from '../../shared/hooks/useCopyToClipboard';
import { FONT_SANS, INK, INK_MUTE } from '../../shared/styles';
import { toast } from '../../shared/toast';
import { btnPrimary, btnSecondary, btnSmall } from '../styles';
import { AiConnectionRows, type AiConnection } from './AiConnectionRows';
import { SettingsCard } from './SettingsCard';
import { TOKEN_CLIENTS, tokenSnippet, type TokenClient } from './aiToolSnippets';

interface Mine { may_connect: boolean; oauth_url: string | null; connections: AiConnection[] }

const text: React.CSSProperties = { fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE, lineHeight: 1.6, margin: 0 };
const field: React.CSSProperties = {
  fontFamily: FONT_SANS, fontSize: 14, padding: '7px 10px', borderRadius: 4,
  border: '1px solid var(--color-ck-line-strong)', background: 'var(--color-ck-card)', color: INK,
};
const codeBlock: React.CSSProperties = {
  display: 'block', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: 12,
  padding: '8px 10px', borderRadius: 6, background: 'var(--color-ck-bg)', color: 'var(--color-ck-ink)',
};
const subhead: React.CSSProperties = { fontFamily: FONT_SANS, fontSize: 14, fontWeight: 600, color: INK, margin: '18px 0 6px' };

async function copy(value: string) {
  if (await copyToClipboard(value)) toast.success('Copied.');
  else toast.error('Could not copy — select it and copy it manually.');
}

export function ConnectAiToolCard({ isMobile }: { isMobile: boolean }) {
  const [mine, setMine] = useState<Mine | null>(null);
  const [name, setName] = useState('');
  const [readOnly, setReadOnly] = useState(false);
  const [lifetime, setLifetime] = useState('90');
  const [busy, setBusy] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const [client, setClient] = useState<TokenClient>('Claude Code');

  const refresh = () => api<Mine>('/api/mcp/connections').then(setMine).catch(() => { /* keep unknown */ });
  useEffect(() => { refresh(); }, []);

  async function create() {
    setBusy(true);
    try {
      const made = await api<{ token: string }>('/api/mcp/tokens', {
        method: 'POST',
        body: JSON.stringify({
          name: name.trim(), read_only: readOnly,
          lifetime_days: lifetime === 'never' ? null : Number(lifetime),
        }),
      });
      setToken(made.token);
      setName('');
      void refresh();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.detail || e.message : 'Could not create the token.');
    } finally {
      setBusy(false);
    }
  }

  const mcpUrl = `${window.location.origin}/mcp`;
  const snippet = tokenSnippet(client, mcpUrl, token ?? 'YOUR_TOKEN');

  return (
    <SettingsCard
      id="connect_ai_tool"
      title="Connect an AI tool"
      description="Let your own ChatGPT, Claude, Claude Code or Codex work in CakeCRM as you. It sees what you can see, and every call it makes is logged."
      isMobile={isMobile}
    >
      {mine === null && <p style={text}>Loading…</p>}

      {mine && !mine.may_connect && (
        <p style={text}>
          An admin has not switched on connecting your own AI tools. Ask them to turn it on
          under Settings → Workspace → AI tool connections.
        </p>
      )}

      {mine?.may_connect && (
        <div>
          <AiConnectionRows
            connections={mine.connections}
            showSeat={false}
            basePath="/api/mcp/connections"
            onRevoked={id => setMine(m => (m ? { ...m, connections: m.connections.filter(c => c.id !== id) } : m))}
          />

          <h3 style={subhead}>ChatGPT or Claude</h3>
          {mine.oauth_url ? (
            <div>
              <p style={text}>Add CakeCRM as a connector with this address, then sign in when it asks:</p>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 6 }}>
                <code style={{ ...codeBlock, flex: 1 }}>{mine.oauth_url}</code>
                <button type="button" onClick={() => void copy(mine.oauth_url!)} style={{ ...btnSecondary, ...btnSmall }}>Copy</button>
              </div>
              <p style={{ ...text, marginTop: 6 }}>
                ChatGPT: turn on developer mode in its settings, create a connector with this
                address and OAuth sign-in. Claude: Settings → Connectors → Add custom
                connector, paste the address, then Connect.
              </p>
            </div>
          ) : (
            <p style={text}>
              Signing in from ChatGPT or Claude needs this install to have a public https
              address. Ask an admin to set one; until then, use a personal token below.
            </p>
          )}

          <h3 style={subhead}>A personal token, for coding tools</h3>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
            <input aria-label="Token name" placeholder="Name, e.g. Claude Code on my laptop" value={name}
              maxLength={60} onChange={e => setName(e.target.value)} style={{ ...field, flex: '1 1 220px' }} />
            <select aria-label="Access" value={readOnly ? 'ro' : 'rw'} onChange={e => setReadOnly(e.target.value === 'ro')} style={field}>
              <option value="rw">Read and write</option>
              <option value="ro">Read only</option>
            </select>
            <select aria-label="Expires" value={lifetime} onChange={e => setLifetime(e.target.value)} style={field}>
              <option value="30">30 days</option>
              <option value="90">90 days</option>
              <option value="365">1 year</option>
              <option value="never">Never</option>
            </select>
            <button type="button" onClick={() => void create()} disabled={busy || !name.trim()}
              style={{ ...btnPrimary, opacity: busy || !name.trim() ? 0.6 : 1 }}>
              Create token
            </button>
          </div>

          {token && (
            <div style={{ marginTop: 12 }}>
              <p style={{ ...text, color: INK }}>Copy your token now. It is not shown again.</p>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 6 }}>
                <code style={{ ...codeBlock, flex: 1 }}>{token}</code>
                <button type="button" onClick={() => void copy(token)} style={{ ...btnSecondary, ...btnSmall }}>Copy</button>
              </div>
            </div>
          )}

          <div role="tablist" aria-label="AI tool" style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 14 }}>
            {TOKEN_CLIENTS.map(c => (
              <button key={c} type="button" role="tab" aria-selected={c === client} onClick={() => setClient(c)}
                style={{ ...btnSecondary, ...btnSmall, ...(c === client ? { color: INK, borderColor: 'var(--color-ck-ink)' } : {}) }}>
                {c}
              </button>
            ))}
          </div>
          <p style={{ ...text, marginTop: 8 }}>{snippet.where}</p>
          <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginTop: 6 }}>
            <code style={{ ...codeBlock, flex: 1 }}>{snippet.code}</code>
            <button type="button" onClick={() => void copy(snippet.code)} style={{ ...btnSecondary, ...btnSmall }}>Copy</button>
          </div>
          {!token && <p style={{ ...text, marginTop: 6 }}>Create a token first; it is filled in here for you.</p>}
        </div>
      )}
    </SettingsCard>
  );
}
