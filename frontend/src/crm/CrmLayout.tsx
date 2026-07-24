import { useState, useEffect, useCallback } from 'react';
import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom';
import { api } from '../core/api/client';
import { useAuth } from '../core/auth/AuthContext';
import { useIsMobile } from '../shared/useIsMobile';
import { MobileMenuDrawer } from '../shared/MobileMenuDrawer';
import { confirmDialog } from '../shared/confirm';
import { INK, INK_SOFT, INK_MUTE, LINE, LINE_STRONG, ACCENT, GOLD, FONT_DISPLAY, FONT_SANS } from '../shared/styles';
import { modalOverlay, modalContent, btnPrimary, btnSecondary } from './styles';

const NAV_ITEMS = [
  { to: '/crm', label: 'Dashboard', end: true },
  { to: '/crm/contacts', label: 'Contacts' },
  { to: '/crm/companies', label: 'Companies' },
  { to: '/crm/pipeline', label: 'Pipeline' },
  { to: '/crm/tasks', label: 'Tasks' },
];

interface DemoStatus {
  empty: boolean;
  sample_data_loaded: boolean;
  show_onboarding: boolean;
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
          <p style={{ fontFamily: FONT_SANS, fontSize: 13, color: '#C24141', margin: '12px 0 0' }}>
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
      background: 'rgba(176,124,46,0.08)',
      borderBottom: '1px solid rgba(176,124,46,0.15)',
      padding: isMobile ? '10px 16px' : '8px 28px',
      display: 'flex',
      flexDirection: isMobile ? 'column' : 'row',
      alignItems: isMobile ? 'flex-start' : 'center',
      justifyContent: 'space-between',
      gap: isMobile ? 8 : 16,
    }}>
      <span style={{
        fontFamily: FONT_SANS, fontSize: 13, color: error ? '#C24141' : GOLD, lineHeight: 1.4,
      }}>
        {error
          ? 'Failed to clear example data. Please try again.'
          : <>You're viewing example data &mdash; contacts, deals, and tasks are samples.</>}
      </span>
      <button
        onClick={handleClear}
        disabled={clearing}
        style={{
          background: 'rgba(176,124,46,0.12)', color: GOLD,
          border: '1px solid rgba(176,124,46,0.20)', borderRadius: 4,
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

export function CrmLayout() {
  const isMobile = useIsMobile();
  const navigate = useNavigate();
  const { logout } = useAuth();
  const [showMenu, setShowMenu] = useState(false);
  const [status, setStatus] = useState<DemoStatus | null>(null);
  const [onboardDone, setOnboardDone] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    // best-effort: assume nothing to prompt on failure
    api<DemoStatus>('/api/crm/demo-status')
      .then(setStatus)
      .catch(() => setStatus({ empty: false, sample_data_loaded: false, show_onboarding: false }));
  }, []);

  const handleLoadSample = useCallback(async () => {
    await api('/api/crm/load-sample-data', { method: 'POST' });
    setStatus({ empty: false, sample_data_loaded: true, show_onboarding: false });
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

  return (
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
              <span style={{ fontSize: 22 }}>🍰</span>
              <span style={{
                fontFamily: FONT_DISPLAY,
                fontSize: isMobile ? 19 : 18, letterSpacing: '-0.01em', color: INK,
              }}>CakeCRM</span>
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
                <Link to="/setup" style={actionLink}>AI Setup</Link>
                <button onClick={logout} style={actionLink}>Sign out</button>
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

      {status?.sample_data_loaded && (
        <DemoBanner onClear={handleClearDemo} isMobile={isMobile} />
      )}

      <div key={refreshKey} style={{ flex: 1, overflow: 'auto', position: 'relative' }}>
        <Outlet />
      </div>
    </div>
  );
}
