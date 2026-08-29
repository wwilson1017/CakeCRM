/**
 * The dashboard Today panel (issue #130): one ranked list of what needs you today.
 *
 * Shaped after `WeeklyTouchesCard` — it owns its wrapper padding so hiding leaves no
 * gap, guards responses with a monotonic request id, and keeps the last good payload on
 * a failed refetch. The ranking is entirely the server's: `items` arrives ordered by the
 * priority ladder and is rendered in that order, never re-sorted here. The client does
 * no date arithmetic at all — even due labels compare against the payload's own `date`,
 * so a browser in a different timezone than the server still agrees with the bucketing.
 */
import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
import { useNavigate } from 'react-router-dom';

import { api } from '../../core/api/client';
import { useAuth } from '../../core/auth/AuthContext';
import type { CrmToday, CrmTodayItem } from '../../core/types';
import { loadPersistedState, savePersistedState } from '../../shared/search/persist';
import {
  ACCENT, FONT_DISPLAY, INK, INK_DIM, INK_MUTE, LINE, SAGE, mono,
} from '../../shared/styles';
import { toast } from '../../shared/toast';
import { dueLabel, parseUTC } from '../gtd/util';
import { cardStyle, sectionHeading } from '../styles';
import {
  TODAY_SCOPE_KEY, coerceTodayScope, collapseToday, msUntilRefresh, whyBadge,
  type TodayScope,
} from '../todayPanel';
import { UNASSIGNED_LABEL, useUsers } from '../useUsers';

interface Props {
  wrapperStyle?: CSSProperties;
  /** Bumped by the page's reload() so a mutation anywhere on the dashboard refreshes
   *  this card with the rest — the same mechanism WeeklyTouchesCard uses. */
  refreshKey?: number;
  /** Called after this panel completes a task, so the page can refresh every card. */
  onMutated?: () => void;
}

const scopeButton = (active: boolean): CSSProperties => ({
  ...mono(9, active ? INK : INK_DIM),
  background: 'none',
  border: 'none',
  padding: '2px 6px',
  cursor: 'pointer',
  borderBottom: `1px solid ${active ? ACCENT : 'transparent'}`,
});

export function TodayPanel({ wrapperStyle, refreshKey, onMutated }: Props) {
  const [data, setData] = useState<CrmToday | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [scope, setScope] = useState<TodayScope>(
    () => loadPersistedState(TODAY_SCOPE_KEY, coerceTodayScope),
  );
  // Bumped to force a refetch that no input change would otherwise express: the
  // midnight rollover, where the deps are all identical to the previous run.
  const [reloadTick, setReloadTick] = useState(0);
  const reqId = useRef(0);
  const navigate = useNavigate();
  const { users } = useUsers();
  const { currentUser } = useAuth();
  const meId = currentUser?.id ?? null;

  useEffect(() => {
    // "Mine" needs an id to ask for. /api/me may still be in flight; this effect re-runs
    // when it lands, so waiting is correct and asking for everyone would be wrong.
    if (scope === 'mine' && meId === null) return;
    const id = ++reqId.current;
    const qs = scope === 'mine' ? `?owner_id=${meId}` : '';
    api<CrmToday>(`/api/crm/dashboard/today${qs}`)
      .then(res => {
        if (id !== reqId.current) return;
        setData(res);
        setLoading(false);
      })
      .catch(() => {
        if (id !== reqId.current) return;
        // A refetch failure keeps the last good payload on screen; a first-load failure
        // leaves the panel hidden rather than putting an error box at the top of the
        // dashboard, which is the WeeklyTouchesCard triage.
        setLoading(false);
      });
  }, [scope, meId, refreshKey, reloadTick]);

  // Reload when the SERVER's day rolls over. Deliberately not `useLocalDay`: that fires
  // at the BROWSER's midnight, and on a default install (TIMEZONE unset, so the server
  // is on UTC) the two are hours apart — a tab open across the real boundary would keep
  // showing yesterday's list, which is the very failure that hook exists to prevent.
  useEffect(() => {
    if (!data) return;
    const timer = setTimeout(
      () => setReloadTick(t => t + 1),
      msUntilRefresh(data.next_refresh_at, Date.now()),
    );
    return () => clearTimeout(timer);
    // `reloadTick` re-arms the timer even when the new payload is byte-identical to the
    // old one: `setState` with an equal value is a React bail-out, so keying only on
    // `data` would strand the panel permanently if a fire ever landed before the server
    // rolled over (clock skew, an early wake).
  }, [data, reloadTick]);

  const chooseScope = useCallback((next: TodayScope) => {
    setScope(next);
    savePersistedState(TODAY_SCOPE_KEY, next);
  }, []);

  const complete = useCallback(async (id: number) => {
    try {
      await api(`/api/crm/tasks/${id}/complete`, { method: 'PUT' });
    } catch {
      // Includes a 404: the row is already gone, and the refetch below clears it.
      toast.error('Could not complete that task.');
    }
    // Refetch either way — a repeating task spawns its successor server-side, and a
    // failure may still have committed. onMutated refreshes the sibling cards too.
    setReloadTick(t => t + 1);
    onMutated?.();
  }, [onMutated]);

  const open = useCallback((item: CrmTodayItem) => {
    // Tasks have no detail URL: /crm/tasks is mode-routed (GTD's Today view by default
    // since #102, the list in normal mode). Both are coherent destinations, and the
    // row's own checkbox is how you act on that specific task without leaving.
    navigate(item.kind === 'reminder' ? '/crm/reminders' : '/crm/tasks');
  }, [navigate]);

  if (loading && !data) return null;  // no flash at the top of the dashboard
  if (!data) return null;

  const { visible, hiddenCount } = collapseToday(data.items, expanded);
  const multiSeat = users.length > 1;
  // The rows on screen may still describe the previous scope while a switch is in
  // flight. Saying so beats silently showing one scope's rows under another's label.
  const settling = scope === 'mine' ? data.scope.owner_id === null : data.scope.owner_id !== null;

  return (
    <div style={wrapperStyle}>
      <div style={{ ...cardStyle, padding: '18px 20px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 10 }}>
          <h2 style={{ ...sectionHeading(INK_DIM), margin: 0 }}>Today</h2>
          {multiSeat && (
            <div role="group" aria-label="Owner scope" style={{ marginLeft: 'auto', display: 'flex', gap: 2 }}>
              <button type="button" aria-pressed={scope === 'mine'} style={scopeButton(scope === 'mine')}
                      onClick={() => chooseScope('mine')}>MINE</button>
              <button type="button" aria-pressed={scope === 'everyone'} style={scopeButton(scope === 'everyone')}
                      onClick={() => chooseScope('everyone')}>EVERYONE</button>
            </div>
          )}
        </div>

        {data.items.length === 0 ? (
          <p style={{ margin: 0, fontSize: 13, color: INK_DIM }}>Nothing needs you today.</p>
        ) : (
          <div aria-busy={settling || undefined} style={{ opacity: settling ? 0.55 : 1 }}>
            {visible.map(item => (
              <TodayRow key={`${item.kind}-${item.id}`} item={item} today={data.date}
                        showUnassigned={multiSeat} onOpen={open} onComplete={complete} />
            ))}
          </div>
        )}

        {hiddenCount > 0 && (
          <button type="button" onClick={() => setExpanded(true)}
                  style={{ ...mono(10, INK_MUTE), background: 'none', border: 'none', padding: '10px 0 0', cursor: 'pointer' }}>
            +{hiddenCount} more today
          </button>
        )}
        {expanded && data.items.length > 5 && (
          <button type="button" onClick={() => setExpanded(false)}
                  style={{ ...mono(10, INK_MUTE), background: 'none', border: 'none', padding: '10px 0 0', cursor: 'pointer' }}>
            Show less
          </button>
        )}
      </div>
    </div>
  );
}

interface RowProps {
  item: CrmTodayItem;
  today: string;
  showUnassigned: boolean;
  onOpen: (item: CrmTodayItem) => void;
  onComplete: (id: number) => void;
}

/**
 * Two SIBLING controls, not a button nested inside a clickable row.
 *
 * `WeeklyTouchesCard`'s whole-row `role="button"` has no interactive child; this row
 * does. Nesting them would put a control inside a control for assistive tech, and Space
 * on the checkbox would bubble a keydown to the row and navigate away — an `onClick`
 * `stopPropagation` fires too late to prevent that.
 */
function TodayRow({ item, today, showUnassigned, onOpen, onComplete }: RowProps) {
  const badge = whyBadge(item);
  const due = item.kind === 'task' ? dueLabel(item.due_date, today) : null;
  const unassigned = item.kind === 'task' && item.owner_id === null && showUnassigned;

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 0', borderBottom: `1px solid ${LINE}` }}>
      {item.kind === 'task' && (
        <button type="button" aria-label={`Complete ${item.title}`} onClick={() => onComplete(item.id)}
                style={{ flexShrink: 0, width: 18, height: 18, padding: 0, borderRadius: 4, cursor: 'pointer',
                         border: `1px solid ${LINE}`, background: 'none', display: 'flex',
                         alignItems: 'center', justifyContent: 'center', color: SAGE }} />
      )}
      <button type="button" onClick={() => onOpen(item)}
              style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 10, background: 'none',
                       border: 'none', padding: 0, cursor: 'pointer', textAlign: 'left', font: 'inherit' }}>
        <span style={{ ...mono(9, badge.color), flexShrink: 0 }}>{badge.label}</span>
        <span style={{ flex: 1, minWidth: 0, fontSize: 13, color: INK, overflow: 'hidden',
                       textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: FONT_DISPLAY }}>
          {item.title}
        </span>
        {unassigned && <span style={{ ...mono(9, INK_DIM), flexShrink: 0 }}>{UNASSIGNED_LABEL}</span>}
        <span style={{ ...mono(9, INK_MUTE), flexShrink: 0 }}>
          {item.kind === 'reminder' ? reminderTime(item.due_at) : due?.text}
        </span>
      </button>
    </div>
  );
}

/** An instant renders correctly in any browser timezone. Parsed via `gtd/util.parseUTC`
 *  rather than `new Date()`: Postgres emits six fractional digits and Safari need not
 *  parse that form, which would render "Invalid Date". */
function reminderTime(dueAt: string): string {
  const parsed = parseUTC(dueAt);
  return Number.isNaN(parsed.getTime())
    ? ''
    : parsed.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}
