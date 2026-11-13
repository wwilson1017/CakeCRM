/**
 * The list of connected AI tools (#322), shared by a member's own card and the admin's
 * all-seats card: what each connection is, when it was made and last used, its call log
 * on demand, and Revoke. The parent says which routes to drive.
 */

import { useState } from 'react';

import { api } from '../../core/api/client';
import { FONT_SANS, INK, INK_MUTE } from '../../shared/styles';
import { toast } from '../../shared/toast';
import { parseUTC } from '../gtd/util';
import { btnSecondary, btnSmall } from '../styles';

export interface AiConnection {
  id: number;
  kind: 'token' | 'oauth';
  name: string;
  read_only: boolean;
  created_at: string;
  last_used_at: string;
  expires_at: string | null;
  user_name?: string;
  user_email?: string;
}

interface AiCall {
  id: number;
  tool: string;
  detail: string;
  ok: boolean;
  refused: string | null;
  duration_ms: number;
  at: string;
}

const day = (iso: string) => parseUTC(iso).toLocaleDateString();
const when = (iso: string) =>
  parseUTC(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });

const chip: React.CSSProperties = {
  fontFamily: FONT_SANS, fontSize: 11, padding: '1px 7px', borderRadius: 10,
  border: '1px solid var(--color-ck-line-strong)', color: INK_MUTE, whiteSpace: 'nowrap',
};
const text: React.CSSProperties = { fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE, lineHeight: 1.6, margin: 0 };

function outcome(c: AiCall): string {
  if (c.refused === 'read_only') return 'refused: read-only';
  if (c.refused) return 'refused';
  return c.ok ? 'ok' : 'error';
}

export function AiConnectionRows({ connections, showSeat, basePath, onRevoked }: {
  connections: AiConnection[];
  showSeat: boolean;
  /** `/api/mcp/connections` or `/api/mcp/admin/connections`. */
  basePath: string;
  onRevoked: (id: number) => void;
}) {
  const [open, setOpen] = useState<number | null>(null);
  // Keyed by connection, so a slow response for one row never shows under another.
  const [loaded, setLoaded] = useState<{ id: number; calls: AiCall[] } | null>(null);

  async function toggleCalls(id: number) {
    if (open === id) { setOpen(null); return; }
    setOpen(id);
    try {
      setLoaded({ id, calls: (await api<{ calls: AiCall[] }>(`${basePath}/${id}/calls`)).calls });
    } catch {
      setLoaded({ id, calls: [] });
      toast.error('Could not load the call log.');
    }
  }

  async function revoke(c: AiConnection) {
    if (!window.confirm(`Disconnect ${c.name}? It stops working at once.`)) return;
    try {
      await api(`${basePath}/${c.id}`, { method: 'DELETE' });
      onRevoked(c.id);
      toast.success(`${c.name} disconnected.`);
    } catch {
      toast.error('Could not disconnect it.');
    }
  }

  // Only one row is open at a time; its log shows only once ITS response has arrived.
  const calls = loaded?.id === open ? loaded.calls : null;

  if (connections.length === 0) return <p style={text}>No AI tools are connected.</p>;

  return (
    <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
      {connections.map(c => (
        <li key={c.id} style={{ borderTop: '1px solid var(--color-ck-line)', padding: '10px 0' }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
            <strong style={{ fontFamily: FONT_SANS, fontSize: 14, color: INK }}>{c.name}</strong>
            <span style={chip}>{c.kind === 'oauth' ? 'Signed in' : 'Token'}</span>
            {c.read_only && <span style={chip}>Read only</span>}
            {showSeat && <span style={{ ...text, fontSize: 12 }}>{c.user_name || c.user_email}</span>}
            <span style={{ flex: 1 }} />
            <button type="button" onClick={() => void toggleCalls(c.id)} style={{ ...btnSecondary, ...btnSmall }}>
              {open === c.id ? 'Hide calls' : 'Calls'}
            </button>
            <button type="button" onClick={() => void revoke(c)} style={{ ...btnSecondary, ...btnSmall }}>
              Revoke
            </button>
          </div>
          <p style={{ ...text, fontSize: 12, marginTop: 4 }}>
            Connected {day(c.created_at)} · last used {when(c.last_used_at)}
            {c.kind === 'token' && ` · ${c.expires_at ? `expires ${day(c.expires_at)}` : 'never expires'}`}
          </p>
          {open === c.id && (
            <div style={{ marginTop: 8, overflowX: 'auto' }}>
              {calls === null && <p style={text}>Loading…</p>}
              {calls?.length === 0 && <p style={text}>No calls in the last 90 days.</p>}
              {calls && calls.length > 0 && (
                <table style={{ ...text, fontSize: 12, borderCollapse: 'collapse', width: '100%' }}>
                  <thead>
                    <tr style={{ textAlign: 'left' }}>
                      <th style={{ padding: '2px 8px 2px 0' }}>When</th>
                      <th style={{ padding: '2px 8px 2px 0' }}>Tool</th>
                      <th style={{ padding: '2px 8px 2px 0' }}>Record</th>
                      <th style={{ padding: '2px 8px 2px 0' }}>Result</th>
                      <th style={{ padding: '2px 0' }}>ms</th>
                    </tr>
                  </thead>
                  <tbody>
                    {calls.map(k => (
                      <tr key={k.id}>
                        <td style={{ padding: '2px 8px 2px 0', whiteSpace: 'nowrap' }}>{when(k.at)}</td>
                        <td style={{ padding: '2px 8px 2px 0' }}><code>{k.tool}</code></td>
                        <td style={{ padding: '2px 8px 2px 0' }}>{k.detail}</td>
                        <td style={{ padding: '2px 8px 2px 0' }}>{outcome(k)}</td>
                        <td style={{ padding: '2px 0' }}>{k.duration_ms}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}
