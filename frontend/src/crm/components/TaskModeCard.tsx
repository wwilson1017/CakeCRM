/**
 * TaskModeCard — pick the task experience, and manage the two no-login todo
 * surfaces (#70).
 *
 * Todo-GTD is the DEFAULT since #102, so this card frames it first and presents the
 * flat list as the simpler opt-out — including for the installs #102's migration
 * flipped, whose owners arrive here looking for exactly that.
 *
 * Switching modes migrates nothing: GTD is a view over the same task rows, so the
 * change is instant and losslessly reversible. The card says so plainly, because
 * "switch task system" otherwise reads like a destructive operation.
 *
 * The mode itself is NOT local state (#102): CrmLayout owns it for the whole CRM, and
 * a second copy here meant a switch did not reach /crm/tasks until a page reload.
 *
 * The public-surface half is deliberately blunt about what each link exposes. A
 * tokenless capture URL is a write-only inbox drop that anyone with the address can
 * post to; a tokenless todo URL hands over the whole list, read and write. Users
 * should not have to infer that from the word "public".
 */

import { useEffect, useState } from 'react';

import { api } from '../../core/api/client';
import { copyToClipboard } from '../../shared/hooks/useCopyToClipboard';
import { CORAL_TEXT, FONT_SANS, INK_MUTE, labelStyle } from '../../shared/styles';
import { toast } from '../../shared/toast';
import { useSetTaskMode, useTaskMode } from '../gtd/TaskModeContext';
import type { TaskMode } from '../gtd/TaskModeContext';
// #102's body + #103's shell: the card chrome and heading helpers are gone because
// SettingsCard owns both now (settingsSections.test.ts pins that no settings card
// imports them).
import { btnPrimary, btnSecondary, settingsSubheading } from '../styles';
import { SettingsCard } from './SettingsCard';

interface Surfaces {
  todo_capture_token: string;
  todo_web_enabled: boolean;
  todo_web_token: string;
  capture_path: string;
  capture_public: boolean;
  web_path: string | null;
  web_public: boolean;
}

export function TaskModeCard({ isMobile }: { isMobile: boolean }) {
  // Read AND write the mode through the layout that owns it — no local copy (#102).
  // `null` = not known yet, which disables the buttons below.
  const mode = useTaskMode();
  const setMode = useSetTaskMode();
  const [surfaces, setSurfaces] = useState<Surfaces | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Surfaces>('/api/crm/todo-surfaces').then(setSurfaces).catch(() => { /* keep unknown */ });
  }, []);

  async function switchMode(next: TaskMode) {
    if (busy || next === mode) return;
    setBusy(true);
    try {
      await api('/api/crm/task-mode', { method: 'POST', body: JSON.stringify({ mode: next }) });
      setMode(next);
      toast.success(
        next === 'gtd'
          ? 'Todo-GTD mode on. Your existing tasks are all still there, as next actions.'
          : 'Switched to the simple task list. Nothing was lost — your todos are all still there.',
      );
    } catch {
      toast.error('Failed to switch task mode.');
    } finally {
      setBusy(false);
    }
  }

  async function patchSurfaces(body: Record<string, unknown>) {
    if (busy) return;
    setBusy(true);
    try {
      setSurfaces(await api<Surfaces>('/api/crm/todo-surfaces', {
        method: 'POST', body: JSON.stringify(body),
      }));
    } catch {
      toast.error('Failed to update the public todo links.');
    } finally {
      setBusy(false);
    }
  }

  const linkFor = (path: string) => `${window.location.origin}${path}`;

  async function copy(path: string) {
    // Through the shared helper for its non-secure-context fallback: these links
    // exist to be opened on a LAN over plain http, which is exactly where
    // `navigator.clipboard` is undefined — so the bare async API failed on the
    // one deployment this button serves.
    if (await copyToClipboard(linkFor(path))) toast.success('Link copied.');
    else toast.error('Could not copy — select the link and copy it manually.');
  }

  const modeButton = (value: TaskMode, label: string, hint: string) => (
    <button
      type="button"
      onClick={() => void switchMode(value)}
      disabled={busy || mode === null}
      aria-pressed={mode === value}
      style={{
        ...(mode === value ? btnPrimary : btnSecondary),
        flex: 1, textAlign: 'left', padding: '12px 14px',
        opacity: busy || mode === null ? 0.6 : 1,
      }}
    >
      <span style={{ display: 'block', fontWeight: 700 }}>{label}</span>
      <span style={{ display: 'block', fontSize: 12, fontWeight: 400, marginTop: 2 }}>{hint}</span>
    </button>
  );

  return (
    <SettingsCard
      id="todo_mode"
      title="Task mode"
      description="Switching is safe and reversible — both modes read the same tasks. Nothing is migrated, copied or deleted."
      isMobile={isMobile}
    >
      <div style={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', gap: 10 }}>
        {modeButton('gtd', 'Todo-GTD (default)', 'Inbox, contexts, projects, repeats and a weekly review.')}
        {modeButton('normal', 'Simple list', 'Just tasks with due dates and priorities — no inbox or contexts.')}
      </div>

      {/* #102: shown in BOTH modes, because neither surface depends on the task mode.
          They are mounted unconditionally and gated only on their own settings, and
          switching mode does not turn either off (set_task_mode writes task_mode and
          nothing else) — so gating this section on `mode === 'gtd'` hid the controls for
          endpoints that kept serving. Two ways that bit, both made routine by #102
          making "Simple list" the opt-out every flipped install is invited to take: an
          admin who enabled the public read+write todo app could no longer see it was
          live, rotate its token or switch it off; and tokenless `/capture` — publicly
          writable on EVERY install by default — offered no way to add a token and
          restrict it. A reachable surface must never lose its off switch. */}
      {surfaces && (
        <div style={{ marginTop: 20, borderTop: '1px solid var(--color-ck-line)', paddingTop: 16 }}>
          <h3 style={settingsSubheading}>No-login links</h3>

          <div style={{ marginTop: 12 }}>
            <p style={{ ...labelStyle, marginBottom: 4 }}>Quick capture (write-only)</p>
            <p style={{ fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE, margin: '0 0 8px' }}>
              A phone bookmark that drops text straight into your inbox. It cannot read
              anything back.{' '}
              {surfaces.capture_public
                ? <strong style={{ color: CORAL_TEXT }}>Anyone who knows this address can add to your inbox — add a secret link to restrict it.</strong>
                : 'Only someone with the secret link can post to it.'}
            </p>
            <SurfaceRow
              path={surfaces.capture_path}
              linkFor={linkFor}
              onCopy={() => void copy(surfaces.capture_path)}
              onRegenerate={() => void patchSurfaces({ regenerate_capture: true })}
              onClear={surfaces.todo_capture_token
                ? () => void patchSurfaces({ capture_token: '' })
                : undefined}
              busy={busy}
              secretLabel={surfaces.todo_capture_token ? 'Secret link' : 'Public link'}
            />
          </div>

          <div style={{ marginTop: 20 }}>
            <p style={{ ...labelStyle, marginBottom: 4 }}>Full todo app (read and write)</p>
            <p style={{ fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE, margin: '0 0 8px' }}>
              The whole todo app, with no login. Off by default.{' '}
              {surfaces.todo_web_enabled && surfaces.web_public && (
                <strong style={{ color: CORAL_TEXT }}>
                  Anyone who knows this address can read and edit every todo. Add a secret link.
                </strong>
              )}
            </p>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: FONT_SANS, fontSize: 14 }}>
              <input
                type="checkbox"
                checked={surfaces.todo_web_enabled}
                disabled={busy}
                onChange={e => void patchSurfaces({
                  web_enabled: e.target.checked,
                  // Turning it on without a token would publish the whole list, so mint
                  // one in the same request unless the user already chose one.
                  ...(e.target.checked && !surfaces.todo_web_token ? { regenerate_web: true } : {}),
                })}
              />
              Enable the no-login todo app
            </label>
            {surfaces.todo_web_enabled && surfaces.web_path && (
              <div style={{ marginTop: 8 }}>
                <SurfaceRow
                  path={surfaces.web_path}
                  linkFor={linkFor}
                  onCopy={() => void copy(surfaces.web_path!)}
                  onRegenerate={() => void patchSurfaces({ regenerate_web: true })}
                  busy={busy}
                  secretLabel={surfaces.todo_web_token ? 'Secret link' : 'Public link'}
                />
                <p style={{ fontFamily: FONT_SANS, fontSize: 12, color: INK_MUTE, marginTop: 6 }}>
                  Regenerating immediately breaks the old link, including any home-screen
                  app installed from it.
                </p>
              </div>
            )}
          </div>
        </div>
      )}
    </SettingsCard>
  );
}

function SurfaceRow({ path, linkFor, onCopy, onRegenerate, onClear, busy, secretLabel }: {
  path: string;
  linkFor: (p: string) => string;
  onCopy: () => void;
  onRegenerate: () => void;
  onClear?: () => void;
  busy: boolean;
  secretLabel: string;
}) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
      <code style={{
        flex: '1 1 240px', minWidth: 0, overflowWrap: 'anywhere',
        fontSize: 12, padding: '6px 8px', borderRadius: 6,
        background: 'var(--color-ck-bg)', color: 'var(--color-ck-ink)',
      }}>{linkFor(path)}</code>
      <span style={{ fontFamily: FONT_SANS, fontSize: 12, color: INK_MUTE }}>{secretLabel}</span>
      <button type="button" onClick={onCopy} disabled={busy} style={btnSecondary}>Copy</button>
      <button type="button" onClick={onRegenerate} disabled={busy} style={btnSecondary}>
        New secret link
      </button>
      {onClear && (
        <button type="button" onClick={onClear} disabled={busy} style={btnSecondary}>
          Make public
        </button>
      )}
    </div>
  );
}
