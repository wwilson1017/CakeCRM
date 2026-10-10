// CakeCRM — what an in-flight Baker turn is doing (#282): a phase label and the time it
// has run, on the turn's bubble for the WHOLE turn. The clock counts from the server's
// `started_at`, so it is right after a reattach or a reload.

import { useEffect, useState } from 'react';

import { AI_FILL, INK_SOFT } from '../shared/styles';
import type { WorkingState } from './types';

function workingLabel(w: WorkingState): string {
  switch (w.phase) {
    case 'writing': return 'Writing';
    case 'tool': return w.tool ? `Running ${w.tool}` : 'Running a tool';
    case 'reconnecting': return 'Reconnecting…';
    case 'stopping': return 'Stopping…';
    default: return 'Working';
  }
}

function elapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

export function WorkingIndicator({ working }: { working: WorkingState }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <div role="status" style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6, color: INK_SOFT, fontSize: 12 }}>
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: AI_FILL }} />
      <span>{workingLabel(working)}</span>
      <span style={{ fontVariantNumeric: 'tabular-nums' }}>{elapsed(now - working.startedAt)}</span>
    </div>
  );
}
