/**
 * AiToolConnectionsCard — the admin's view of connected AI tools (#322).
 *
 * The switch "Members may connect their own AI tools" (off by default; admins can always
 * connect) and every seat's connections, each with its call log and Revoke. Turning the
 * switch off stops every member connection on its next call without deleting it, so
 * turning it back on restores them. Admin-only: every route it drives is `require_admin`.
 */

import { useEffect, useState } from 'react';

import { api } from '../../core/api/client';
import { FONT_SANS, INK, INK_MUTE } from '../../shared/styles';
import { toast } from '../../shared/toast';
import { AiConnectionRows, type AiConnection } from './AiConnectionRows';
import { SettingsCard } from './SettingsCard';

interface All { members_enabled: boolean; connections: AiConnection[] }

const text: React.CSSProperties = { fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE, lineHeight: 1.6, margin: 0 };

export function AiToolConnectionsCard({ isMobile }: { isMobile: boolean }) {
  const [all, setAll] = useState<All | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<All>('/api/mcp/admin/connections').then(setAll).catch(() => { /* keep unknown */ });
  }, []);

  async function setSwitch(on: boolean) {
    setBusy(true);
    try {
      const r = await api<{ members_enabled: boolean }>('/api/mcp/admin/settings', {
        method: 'PUT', body: JSON.stringify({ members_enabled: on }),
      });
      setAll(a => (a ? { ...a, members_enabled: r.members_enabled } : a));
    } catch {
      toast.error('Could not change the setting.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <SettingsCard
      id="ai_tool_connections"
      title="AI tool connections"
      description="Who has connected their own ChatGPT, Claude or coding tool to CakeCRM, and whether members may."
      isMobile={isMobile}
    >
      {all === null && <p style={text}>Loading…</p>}
      {all && (
        <div>
          <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, fontFamily: FONT_SANS, fontSize: 14, color: INK, cursor: 'pointer' }}>
            <input type="checkbox" checked={all.members_enabled} disabled={busy}
              onChange={e => void setSwitch(e.target.checked)} style={{ marginTop: 3 }} />
            <span>
              Members may connect their own AI tools
              <span style={{ ...text, display: 'block' }}>
                Admins can always connect. Turning this off stops every member's connection
                at once without deleting it; turning it back on restores them.
              </span>
            </span>
          </label>
          <div style={{ marginTop: 14 }}>
            <AiConnectionRows
              connections={all.connections}
              showSeat
              basePath="/api/mcp/admin/connections"
              onRevoked={id => setAll(a => (a ? { ...a, connections: a.connections.filter(c => c.id !== id) } : a))}
            />
          </div>
        </div>
      )}
    </SettingsCard>
  );
}
