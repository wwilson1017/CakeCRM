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
import type {
  CrmToday, CrmTodayItem, CrmTodayTodoItem,
} from '../../core/types';
import { loadPersistedState, savePersistedState } from '../../shared/search/persist';
import {
  ACCENT, FONT_DISPLAY, INK, INK_DIM, INK_MUTE, LINE, SAGE_TEXT, mono,
} from '../../shared/styles';
import { toast } from '../../shared/toast';
import { dealDeepLink } from '../dealDeepLink';
import DealTemperatureIcon from './DealTemperatureIcon';
import { dueLabel } from '../gtd/util';
import { cardStyle, sectionHeading } from '../styles';
import {
  TODAY_MAX_RETRIES, TODAY_SCOPE_KEY, coerceTodayScope, collapseToday, dealEvidence,
  msUntilRefresh, retryDelayMs, whyBadge, type TodayScope,
} from '../todayPanel';
import { UNASSIGNED_LABEL, useUsers } from '../useUsers';

interface Props {
  wrapperStyle?: CSSProperties;
  /** Bumped by the page's reload() so a mutation anywhere on the dashboard refreshes
   *  this card with the rest — the same mechanism WeeklyTouchesCard uses. */
  refreshKey?: number;
  /** Called after this panel completes a todo, so the page can refresh every card. */
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
  // midnight rollover and the retry, where the deps are all identical to the previous run.
  const [reloadTick, setReloadTick] = useState(0);
  const [failures, setFailures] = useState(0);
  const reqId = useRef(0);
  // The scope the rows on screen actually describe. A failed scope switch reverts to it,
  // so the control and the rows can never end up permanently disagreeing.
  const appliedScope = useRef<TodayScope>(scope);
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
        appliedScope.current = scope;
        setData(res);
        setFailures(0);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (id !== reqId.current) return;
        // A refetch failure keeps the last good payload on screen; a first-load failure
        // leaves the panel hidden rather than putting an error box at the top of the
        // dashboard, which is the WeeklyTouchesCard triage. Logged for the same reason
        // that card logs: a panel that silently never renders leaves a self-hoster
        // debugging "my dashboard has no Today panel" with nothing to go on.
        console.error('Failed to load the Today panel:', err);
        setLoading(false);
        setFailures(f => f + 1);
        if (scope !== appliedScope.current) {
          // A failed SCOPE SWITCH is the one failure that would otherwise strand the UI
          // in a lie — the control says one scope while the rows describe another, with
          // nothing left in the deps to retry. Put the control back where the data is.
          // This terminates: the revert refetches the applied scope, and if that fails
          // too the scopes now match, so `setScope` is a no-op React bails out of.
          toast.error('Could not switch scope.');
          setScope(appliedScope.current);
          savePersistedState(TODAY_SCOPE_KEY, appliedScope.current);
        }
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
    // Keyed on `data` ALONE, deliberately. Adding `reloadTick` here would re-arm off the
    // still-stale payload the instant the timer fired: past the boundary the delay
    // clamps to its floor, so a slow or failing refetch would re-arm every 30s and each
    // new tick would invalidate the in-flight response — a request storm that never
    // updates. `useLocalDay` needs its monotonic tick because `setState` with an EQUAL
    // value is a React bail-out; that cannot happen here, since every successful fetch
    // hands `setData` a freshly parsed object with a new identity. A FAILED fetch leaves
    // `data` untouched and so does not re-arm — which is what the retry below is for.
  }, [data]);

  // Bounded retry, because every other path out of a failed load is incidental: a first
  // load that fails leaves `data` null, which hides the panel AND blocks the timer above,
  // so without this the dashboard's headline feature stays silently absent for the life
  // of the mount. Capped rather than indefinite — this is a dashboard card, not a poller.
  useEffect(() => {
    if (failures === 0 || failures > TODAY_MAX_RETRIES) return;
    const timer = setTimeout(() => setReloadTick(t => t + 1), retryDelayMs(failures));
    return () => clearTimeout(timer);
  }, [failures]);

  const chooseScope = useCallback((next: TodayScope) => {
    setScope(next);
    savePersistedState(TODAY_SCOPE_KEY, next);
  }, []);

  const complete = useCallback(async (id: number) => {
    try {
      await api(`/api/crm/todos/${id}/complete`, { method: 'PUT' });
    } catch {
      // Includes a 404: the row is already gone, and the refetch below clears it.
      toast.error('Could not complete that todo.');
    }
    // Refetch either way — a repeating todo spawns its successor server-side, and a
    // failure may still have committed. onMutated refreshes the sibling cards too.
    setReloadTick(t => t + 1);
    onMutated?.();
  }, [onMutated]);

  const open = useCallback((item: CrmTodayItem) => {
    // A deal DOES have a URL: #145's `?deal=` deep link opens its sheet over the board.
    // Built through `dealDeepLink` rather than written out here, because that module is
    // the browser's half of a shape pinned against `backend/crm/links.py` — a second
    // literal would be a second producer nothing checks.
    if (item.kind === 'deal') { navigate(dealDeepLink(item.id)); return; }
    // Todos have no detail URL: /crm/todos is mode-routed (GTD's Today view by default
    // since #102, the list in normal mode). Both are coherent destinations, and the
    // row's own checkbox is how you act on that specific todo without leaving.
    navigate('/crm/todos');
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

        {/* The settling wrapper covers the EMPTY state too: switching from a scope with
            no rows would otherwise keep asserting "Nothing needs you today" — a claim
            about the scope you just left — with no sign a request was in flight. */}
        <div aria-busy={settling || undefined} style={{ opacity: settling ? 0.55 : 1 }}>
          {/* Keyed on what is actually on screen, not on the payload's length: a card
              holding only recently-touched hot deals has rows, but none of them ranked,
              so it correctly reads "nothing needs you today" above a "+N more" expander. */}
          {visible.length === 0 ? (
            <p style={{ margin: 0, fontSize: 13, color: INK_DIM }}>Nothing needs you today.</p>
          ) : (
            visible.map(item => (
              <TodayRow key={`${item.kind}-${item.id}`} item={item} today={data.date}
                        showUnassigned={multiSeat} onOpen={open} onComplete={complete} />
            ))
          )}
        </div>

        {!expanded && hiddenCount > 0 && (
          <button type="button" onClick={() => setExpanded(true)}
                  style={{ ...mono(10, INK_MUTE), background: 'none', border: 'none', padding: '10px 0 0', cursor: 'pointer' }}>
            +{hiddenCount} more today
          </button>
        )}
        {/* `hiddenCount` answers "what would the expander reveal", so it drives both
            controls. `items.length > 5` cannot: the hidden rows may be unranked rather
            than merely past the fifth slot, and then there is no Show less to click back. */}
        {expanded && hiddenCount > 0 && (
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
  const due = item.kind === 'todo' ? dueLabel(item.due_date, today) : null;
  // Deals carry an owner too (#60), and an unowned hot deal is exactly the row somebody
  // has to pick up — the same reason the todos wear this.
  const unassigned = item.owner_id === null && showUnassigned;

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 0', borderBottom: `1px solid ${LINE}` }}>
      {item.kind === 'todo' ? (
        <button type="button" aria-label={`Complete ${item.title}`} onClick={() => onComplete(item.id)}
                style={{ flexShrink: 0, width: 18, height: 18, padding: 0, borderRadius: 4, cursor: 'pointer',
                         border: `1px solid ${LINE}`, background: 'none', display: 'flex',
                         alignItems: 'center', justifyContent: 'center', color: SAGE_TEXT }} />
      ) : (
        // Only todos can be completed, but the badges still have to line up: without
        // this spacer a deal row starts a checkbox-width to the left of every todo row,
        // and the panel reads as misaligned rather than as two kinds of row.
        <span aria-hidden="true" style={{ flexShrink: 0, width: 18 }} />
      )}
      <button type="button" onClick={() => onOpen(item)}
              style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 10, background: 'none',
                       border: 'none', padding: 0, cursor: 'pointer', textAlign: 'left', font: 'inherit' }}>
        {item.kind === 'deal'
          // #125's control, rendered READ-ONLY: no `onCycle`, so it emits a labelled
          // `role="img"` span rather than a button — the panel is a list of what needs you,
          // not a place to re-triage the pipeline, and a tab stop here would sit inside the
          // row's own open-the-deal button. The tier is the literal 'hot' because the query
          // filters on exactly that; nothing else can reach this row.
          ? <DealTemperatureIcon value="hot" />
          : <TextBadge item={item} />}
        <span style={{ flex: 1, minWidth: 0, fontSize: 13, color: INK, overflow: 'hidden',
                       textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: FONT_DISPLAY }}>
          {item.title}
        </span>
        {unassigned && <span style={{ ...mono(9, INK_DIM), flexShrink: 0 }}>{UNASSIGNED_LABEL}</span>}
        <span style={{ ...mono(9, INK_MUTE), flexShrink: 0 }}>
          {item.kind === 'deal' ? dealEvidence(item.days_since_touch, item.value) : due?.text}
        </span>
      </button>
    </div>
  );
}

/** The todo badge. Its own component so the row can hand `whyBadge` an item the
 *  compiler has already narrowed away from deals. */
function TextBadge({ item }: { item: CrmTodayTodoItem }) {
  const badge = whyBadge(item);
  return <span style={{ ...mono(9, badge.color), flexShrink: 0 }}>{badge.label}</span>;
}
