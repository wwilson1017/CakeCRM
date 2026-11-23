import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { api } from '../../core/api/client';
import { GOLD_TEXT, GOLD_FILL, tint } from '../../shared/styles';

export function BackupWarning({ isAdmin }: { isAdmin: boolean }) {
  const [stale, setStale] = useState(false);
  const location = useLocation();
  useEffect(() => {
    if (!isAdmin) return;
    let active = true;
    const refresh = () => {
      api<{ stale: boolean }>('/api/backups/status')
        .then(status => { if (active) setStale(status.stale); })
        .catch(() => { /* best-effort banner */ });
    };
    refresh();
    const timer = window.setInterval(refresh, 300_000);
    return () => { active = false; window.clearInterval(timer); };
  }, [isAdmin, location.pathname, location.search]);
  if (!isAdmin || !stale) return null;
  return <div role="alert" style={{ background: tint(GOLD_FILL, 8), color: GOLD_TEXT, padding: '10px 16px', fontSize: 13 }}>
    No successful off-site backup in more than two days.{' '}
    <Link to="/crm/settings?section=workspace" style={{ color: GOLD_TEXT }}>Check Backups settings</Link>
  </div>;
}
