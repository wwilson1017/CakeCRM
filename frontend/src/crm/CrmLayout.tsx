import { Suspense, useState, useEffect, useCallback } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { api } from '../core/api/client';
import { useAuth } from '../core/auth/AuthContext';
import { useBranding } from '../core/branding/BrandingContext';
import BootFallback from '../core/components/BootFallback';
import ChunkErrorBoundary from '../core/components/ChunkErrorBoundary';
import { useIsMobile } from '../shared/useIsMobile';
import { MobileMenuDrawer } from '../shared/MobileMenuDrawer';
import { confirmDialog } from '../shared/confirm';
import { INK, INK_SOFT, INK_MUTE, LINE, LINE_STRONG, ACCENT, GOLD_FILL, GOLD_TEXT, FONT_DISPLAY, FONT_SANS, CORAL_TEXT, tint } from '../shared/styles';
import { modalOverlay, modalContent, btnPrimary, btnSecondary, LAUNCHER_CLEARANCE_PX } from './styles';
import { AiKeyNudge } from './components/AiKeyNudge';
import { AssistantLauncher } from './components/AssistantLauncher';
import { BrandLogo } from './components/BrandLogo';
import { NotificationsBell } from './components/NotificationsBell';
import { ThemeToggle } from './components/ThemeToggle';
import { ActiveRecordProvider } from './RecordContext';
import { TaskModeContext, TaskModeSetterContext } from './gtd/TaskModeContext';
import type { TaskMode } from './gtd/TaskModeContext';

const NAV_ITEMS = [
  { to: '/crm', label: 'Dashboard', end: true },
  { to: '/crm/pipeline', label: 'Pipeline' },
  { to: '/crm/contacts', label: 'Contacts' },
  { to: '/crm/companies', label: 'Companies' },
  { to: '/crm/tasks', label: 'Tasks' },
  { to: '/crm/reminders', label: 'Reminders' },
  { to: '/crm/reports', label: 'Reports' },
];

interface DemoStatus {
  empty: boolean;
  sample_data_loaded: boolean;
  show_onboarding: boolean;
  ai_key_prompt_dismissed: boolean;
  /**
   * #70. Absent on an older backend — see the `?? 'normal'` at the provider below,
   * which is deliberately NOT the same fallback as a failed fetch.
   */
  task_mode?: TaskMode;
}

/** First-run prompt: offer to load fictional sample data (or start fresh). */
function OnboardingDialog({ onLoad, onDismiss }: {
  onLoad: () => Promise<void>; onDismiss: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const isMobile = useIsMobile();

  async function handleLoad() {
    setLoading(true);
    setError(false);
    try {
      await onLoad();
    } catch {
      setError(true);
      setLoading(false);
    }
  }

  return (
    <div style={modalOverlay(isMobile)}>
      <div style={modalContent(isMobile, 480)} onClick={e => e.stopPropagation()}>
        <h2 style={{
          fontFamily: FONT_DISPLAY, fontSize: 22, fontWeight: 400,
          letterSpacing: '-0.02em', color: INK, margin: '0 0 12px',
        }}>Start with sample data?</h2>
        <p style={{
          fontFamily: FONT_SANS, fontSize: 14, color: INK_MUTE,
          lineHeight: 1.6, margin: 0,
        }}>
          We can load a set of fictional contacts, deals, and tasks so you can see
          how CakeCRM works. You can clear it anytime, or start with an empty CRM.
        </p>
        {error && (
          <p style={{ fontFamily: FONT_SANS, fontSize: 13, color: CORAL_TEXT, margin: '12px 0 0' }}>
            Couldn't load sample data. Please try again.
          </p>
        )}
        <div style={{ display: 'flex', gap: 12, marginTop: 24, justifyContent: 'flex-end' }}>
          <button onClick={onDismiss} disabled={loading} style={btnSecondary}>No thanks, start fresh</button>
          <button
            onClick={handleLoad}
            disabled={loading}
            style={{ ...btnPrimary, opacity: loading ? 0.6 : 1, cursor: loading ? 'wait' : 'pointer' }}
          >{loading ? 'Loading...' : 'Load sample data'}</button>
        </div>
      </div>
    </div>
  );
}

function DemoBanner({ onClear, isMobile }: {
  onClear: () => Promise<void>; isMobile: boolean;
}) {
  const [clearing, setClearing] = useState(false);
  const [error, setError] = useState(false);

  async function handleClear() {
    // The sample flag stays set while the user may have added real records, so
    // clearing wipes the whole CRM — confirm before the destructive action.
    const ok = await confirmDialog({
      title: 'Clear all CRM data?',
      message: "This removes the example data — and anything you've added since. This can't be undone.",
      confirmLabel: 'Clear everything',
      danger: true,
    });
    if (!ok) return;
    setClearing(true);
    setError(false);
    try {
      await onClear();
    } catch {
      setError(true);
      setClearing(false);
    }
  }

  return (
    <div style={{
      background: tint(GOLD_FILL, 8),
      borderBottom: `1px solid ${tint(GOLD_FILL, 15)}`,
      padding: isMobile ? '10px 16px' : '8px 28px',
      display: 'flex',
      flexDirection: isMobile ? 'column' : 'row',
      alignItems: isMobile ? 'flex-start' : 'center',
      justifyContent: 'space-between',
      gap: isMobile ? 8 : 16,
    }}>
      <span style={{
        fontFamily: FONT_SANS, fontSize: 13, color: error ? CORAL_TEXT : GOLD_TEXT, lineHeight: 1.4,
      }}>
        {error
          ? 'Failed to clear example data. Please try again.'
          : <>You're viewing example data &mdash; contacts, deals, and tasks are samples.</>}
      </span>
      <button
        onClick={handleClear}
        disabled={clearing}
        style={{
          background: tint(GOLD_FILL, 12), color: GOLD_TEXT,
          border: `1px solid ${tint(GOLD_FILL, 20)}`, borderRadius: 4,
          padding: '4px 14px', fontSize: 12, fontFamily: FONT_SANS,
          fontWeight: 500, cursor: clearing ? 'wait' : 'pointer',
          opacity: clearing ? 0.6 : 1, whiteSpace: 'nowrap',
        }}
      >{clearing ? 'Clearing...' : 'Clear example data'}</button>
    </div>
  );
}

const actionLink: React.CSSProperties = {
  border: `1px solid ${LINE_STRONG}`, borderRadius: 6, fontSize: 13,
  color: INK_MUTE, padding: '5px 12px', textDecoration: 'none',
  background: 'transparent', cursor: 'pointer', fontFamily: FONT_SANS,
};

// Fail-closed demo-status: prompt nothing (incl. the AI nudge) when the fetch fails.
const DEMO_STATUS_UNKNOWN: DemoStatus = {
  empty: false, sample_data_loaded: false, show_onboarding: false, ai_key_prompt_dismissed: true,
  // A failed fetch must not strand /crm/tasks on a blank screen, so fall back to the
  // default mode rather than leaving it unknown forever. GTD since #102.
  //
  // Note this is NOT the same event as the backend's fail-safe, and the reason matters:
  // get_task_mode() falls back when THE SERVER cannot read crm_meta, whereas this .catch
  // fires on a network blip or a 5xx, where the server may be perfectly healthy and
  // would have said 'normal' on a normal-mode install. So this is not mirroring the
  // backend — it is guessing the product default, which post-#102 is what nearly every
  // install is actually on, and is therefore right far more often than 'normal' was.
  task_mode: 'gtd',
};

interface SetupStatus { ai_ready: boolean; credentials_present: boolean; }

export function CrmLayout() {
  const isMobile = useIsMobile();
  const navigate = useNavigate();
  // Feeds the route boundary's resetKey below: a caught error must clear when the user
  // navigates away from the page that threw, or one crash freezes the content column for the
  // rest of the session while the nav around it keeps working.
  const location = useLocation();
  const { logout, isAdmin } = useAuth();
  const { branding, logoVersion } = useBranding();
  const [showMenu, setShowMenu] = useState(false);
  const [status, setStatus] = useState<DemoStatus | null>(null);
  const [onboardDone, setOnboardDone] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  // AI state: null = unknown (render nothing AI-flavored yet). credentials_present
  // gates the "add a key" nudge; ai_ready drives the assistant launcher.
  const [setup, setSetup] = useState<SetupStatus | null>(null);
  const [aiPromptDone, setAiPromptDone] = useState(false);

  useEffect(() => {
    // best-effort: assume nothing to prompt on failure (fail-closed)
    api<DemoStatus>('/api/crm/demo-status')
      .then(setStatus)
      .catch(() => setStatus(DEMO_STATUS_UNKNOWN));
  }, []);

  useEffect(() => {
    // best-effort: on failure keep setup unknown (null) — the launcher stays inert
    // and the nudge stays suppressed, so we never misroute a user who may have a key.
    api<SetupStatus>('/api/setup/status')
      .then(setSetup)
      .catch(() => { /* keep unknown */ });
  }, []);

  // #102: the Settings card switches the mode, but this layout owns it for the whole
  // CRM and does not refetch on navigation — so the card pushes the new value up here
  // instead of keeping its own copy. Without this, switching mode in Settings left
  // /crm/tasks rendering the old task system until a full page reload.
  //
  // Dropping the update while `status` is still null is correct: the card disables its
  // buttons until the mode is known, so there is nothing to lose.
  const handleSetTaskMode = useCallback((mode: TaskMode) => {
    setStatus(s => (s ? { ...s, task_mode: mode } : s));
  }, []);

  const handleLoadSample = useCallback(async () => {
    await api('/api/crm/load-sample-data', { method: 'POST' });
    setStatus(s => ({ ...(s ?? DEMO_STATUS_UNKNOWN), empty: false, sample_data_loaded: true, show_onboarding: false }));
    setOnboardDone(true);
    setRefreshKey(k => k + 1);
  }, []);

  const handleDismissOnboarding = useCallback(async () => {
    setOnboardDone(true);
    try {
      await api('/api/crm/dismiss-onboarding', { method: 'POST' });
    } catch {
      // non-fatal — the prompt is already closed for this session
    }
    setStatus(s => (s ? { ...s, show_onboarding: false } : s));
  }, []);

  const handleClearDemo = useCallback(async () => {
    await api('/api/crm/demo-clear', { method: 'POST' });
    setStatus(s => (s ? { ...s, sample_data_loaded: false, empty: true } : s));
    setRefreshKey(k => k + 1);
  }, []);

  const handleDismissAiPrompt = useCallback(async () => {
    setAiPromptDone(true);
    try {
      await api('/api/crm/dismiss-ai-prompt', { method: 'POST' });
    } catch {
      // non-fatal — the nudge is already closed for this session
    }
    setStatus(s => (s ? { ...s, ai_key_prompt_dismissed: true } : s));
  }, []);

  // Distinct AI affordances: (1) dismissible first-run nudge — only when NO key is
  // configured and it hasn't been dismissed, and not while the onboarding dialog is up;
  // (2) the persistent launcher, always rendered below.
  const showAiNudge =
    setup !== null && !setup.credentials_present &&
    status !== null && !status.ai_key_prompt_dismissed && !aiPromptDone &&
    !(status.show_onboarding && !onboardDone);
  const aiReady = setup === null ? null : setup.ai_ready;

  return (
    <ActiveRecordProvider>
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <div style={{ borderBottom: `1px solid ${LINE}` }}>
        {/* Row 1: wordmark + nav tabs (desktop) / hamburger (mobile) + account actions */}
        <div style={{
          height: 52, padding: isMobile ? '0 16px' : '0 28px',
          display: 'flex', alignItems: 'center',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            {isMobile && (
              <div
                onClick={() => setShowMenu(!showMenu)}
                style={{ cursor: 'pointer', color: INK_MUTE, fontSize: 18 }}
              >&#9776;</div>
            )}
            <Link to="/crm" style={{
              display: 'flex', alignItems: 'center', gap: 8, textDecoration: 'none',
            }}>
              {branding?.has_logo ? (
                <BrandLogo
                  key={logoVersion}
                  src={`/api/branding/logo?v=${logoVersion}`}
                  alt=""
                  style={{ height: 26, maxWidth: 120, objectFit: 'contain' }}
                  fallback={<img src="/logo-mark.svg" alt="" style={{ height: 26, width: 26 }} />}
                />
              ) : (
                <img src="/logo-mark.svg" alt="" style={{ height: 26, width: 26 }} />
              )}
              <span style={{
                fontFamily: FONT_DISPLAY,
                fontSize: isMobile ? 19 : 18, letterSpacing: '-0.01em', color: INK,
              }}>{branding?.company_name || 'CakeCRM'}</span>
            </Link>
          </div>
          {!isMobile && (
            <>
              <div style={{ width: 1, height: 22, background: LINE_STRONG, margin: '0 20px' }} />
              <div style={{ display: 'flex', gap: 4 }}>
                {NAV_ITEMS.map(item => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    style={({ isActive }) => ({
                      fontSize: 15, padding: '6px 14px',
                      color: isActive ? INK : INK_SOFT,
                      borderBottom: isActive ? `2px solid ${ACCENT}` : '2px solid transparent',
                      cursor: 'pointer', textDecoration: 'none',
                    })}
                  >
                    {item.label}
                  </NavLink>
                ))}
              </div>
              <div style={{ flex: 1 }} />
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <ThemeToggle />
                <NotificationsBell />
                <Link to="/crm/settings" style={actionLink}>Settings</Link>
                {isAdmin && <Link to="/setup" style={actionLink}>AI Setup</Link>}
                <button onClick={logout} style={actionLink}>Sign out</button>
              </div>
            </>
          )}
          {/* Mobile: the account actions live in the drawer, but the bell stays in
              the header so notifications/alerts are reachable on mobile too. */}
          {isMobile && (
            <>
              <div style={{ flex: 1 }} />
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <ThemeToggle />
                <NotificationsBell />
              </div>
            </>
          )}
        </div>

        {/* Row 2: Tabs (mobile only) */}
        {isMobile && (
          <div style={{
            display: 'flex', overflowX: 'auto', padding: '0 16px',
            borderTop: `1px solid ${LINE}`,
            WebkitOverflowScrolling: 'touch',
          }}>
            {NAV_ITEMS.map(item => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                style={({ isActive }) => ({
                  fontSize: 12, padding: '8px 14px', whiteSpace: 'nowrap',
                  color: isActive ? INK : INK_SOFT,
                  borderBottom: isActive ? `2px solid ${ACCENT}` : '2px solid transparent',
                  cursor: 'pointer', textDecoration: 'none',
                })}
              >
                {item.label}
              </NavLink>
            ))}
          </div>
        )}
      </div>

      {status?.show_onboarding && !onboardDone && (
        <OnboardingDialog onLoad={handleLoadSample} onDismiss={handleDismissOnboarding} />
      )}

      {isMobile && showMenu && (
        <MobileMenuDrawer onClose={() => setShowMenu(false)} navigate={navigate} onSignOut={logout} />
      )}

      {/* Both banners lead somewhere admin-only since #60 — demo-clear is a
          destructive global operation, and the AI nudge links to provider setup — so
          a member is shown neither. Same rule as the settings cards: don't offer an
          action that can only 403. */}
      {status?.sample_data_loaded && isAdmin && (
        <DemoBanner onClear={handleClearDemo} isMobile={isMobile} />
      )}

      {showAiNudge && isAdmin && (
        <AiKeyNudge onDismiss={handleDismissAiPrompt} isMobile={isMobile} />
      )}

      {/* LAUNCHER_CLEARANCE: the fixed "Ask Baker" pill (52px tall, 24px off the
          bottom) floats over this scroll container, so without reserved space it
          permanently covers whatever ends up in the bottom-left corner — on the
          Settings page that was the forms' left-aligned submit buttons. Bottom
          padding on the scroll container lets every page scroll PAST the pill
          instead — see LAUNCHER_CLEARANCE_PX for the arithmetic — keeping the
          launcher itself always
          visible and reachable. Applied on desktop too: the pill overlaps the
          content column there just the same, only with more room around it. */}
      <div key={refreshKey} style={{ flex: 1, overflow: 'auto', position: 'relative', paddingBottom: LAUNCHER_CLEARANCE_PX }}>
        {/* The task mode rides the demo-status payload this layout already fetches,
            so /crm/tasks costs no extra request to decide which task system to show.

            `?? 'normal'` is deliberately NOT the 'gtd' fallback used for a FAILED fetch
            (DEMO_STATUS_UNKNOWN above). These answer different questions: a failed
            fetch means the mode is unknown, so mirror the product default; an ABSENT
            field on a SUCCESSFUL response means a backend that predates #70 and has no
            GTD endpoints at all, where normal is the only mode that renders a working
            page — 'gtd' would render a shell whose every request 404s. */}
        <TaskModeContext.Provider value={status ? (status.task_mode ?? 'normal') : null}>
          <TaskModeSetterContext.Provider value={handleSetTaskMode}>
            {/* Route chunks load here (#149), INSIDE the chrome: a page's first visit — or the
                task mode flipping from unknown to GTD, which is a plain setState and not a
                router transition — suspends only the content column, so the nav stays put
                and this layout's demo-status/setup fetches run in parallel with the download
                instead of after it.
                The boundary is the other half, and it is NOT redundant with Root's: Suspense
                catches a PENDING chunk, never a REJECTED one, so after a deploy (which replaces
                dist wholesale — all 14 route chunks 404 at once) a first visit to any page would
                otherwise throw past this, past App's Suspense, past ConfirmHost/ToastViewport,
                and take the whole shell down. scope="route" keeps the failure in the content
                column while still allowing the one-shot deploy-skew reload, because unlike the
                assistant drawer the user IS blocked: they asked for this page. */}
            <ChunkErrorBoundary scope="route" resetKey={location.pathname}>
              <Suspense fallback={<BootFallback variant="panel" />}>
                <Outlet />
              </Suspense>
            </ChunkErrorBoundary>
          </TaskModeSetterContext.Provider>
        </TaskModeContext.Provider>
      </div>

      {/* Persistent assistant affordance — always present, degrades gracefully. */}
      <AssistantLauncher aiReady={aiReady} />
    </div>
    </ActiveRecordProvider>
  );
}
