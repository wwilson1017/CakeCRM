// CakeCRM — the rows of a change list on its card (#371).
//
// One Approve card covers many deal and todo field changes. This lists every change
// (old → new), every row that will not change with the reason, and, once approved, what
// was applied, with Undo for the whole batch. The rows are fetched from the server's
// staged batch by id, never taken from the model's arguments, so the old values and the
// record names are the database's, and a reloaded conversation shows the batch's real
// state (waiting, applied or undone). A value row carries its own currency on each side;
// nothing is ever added up.

import { useEffect, useState } from 'react';

import { api, ApiError } from '../core/api/client';
import { ACCENT_TEXT, INK, INK_MUTE, INK_SOFT, LINE } from '../shared/styles';
import { toast } from '../shared/toast';
import { COLLAPSED_ROWS } from './bulkItems';

interface ChangeRow {
  record_type: string;
  id: number;
  field: string;
  label: string;
  old: unknown;
  new: unknown;
  state: 'ready' | 'refused' | 'applied' | 'skipped' | 'undone' | 'kept';
  reason?: string;
  /** A deal `value` row only: the deal's currency before and after this batch. */
  currency?: [string, string];
}

interface ChangeBatch {
  id: string;
  status: 'proposed' | 'applied' | 'undone';
  reason: string;
  rows: ChangeRow[];
}

const URL = '/api/assistant/change-batches';
const NOT_CHANGED = new Set<ChangeRow['state']>(['refused', 'skipped']);

function show(value: unknown, unit?: string): string {
  if (value == null || value === '') return '(empty)';
  const text = typeof value === 'boolean' ? (value ? 'yes' : 'no')
    : typeof value === 'number' ? value.toLocaleString() : String(value);
  return unit ? `${text} ${unit}` : text;
}

function Value({ value, unit }: { value: unknown; unit?: string }) {
  const full = show(value, unit);
  return <span title={full}>{full.length > 120 ? `${full.slice(0, 119)}…` : full}</span>;
}

function Description({ row }: { row: ChangeRow }) {
  if (NOT_CHANGED.has(row.state)) return row.reason ?? 'not changed';
  return <>
    <Value value={row.old} unit={row.currency?.[0]} /> → <Value value={row.new} unit={row.currency?.[1]} />
    {row.state === 'kept' && ` (not restored: ${row.reason})`}
  </>;
}

function Rows({ rows }: { rows: ChangeRow[] }) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? rows : rows.slice(0, COLLAPSED_ROWS);
  return (
    <>
      <ul style={{ margin: 0, paddingLeft: 20, color: INK_MUTE, wordBreak: 'break-word' }}>
        {shown.map((row, i) => (
          // Keyed by position: two records can share a name.
          <li key={i}>
            <span style={{ color: INK }}>{row.label}</span>
            <span style={{ color: INK_SOFT }}>{row.field ? ` · ${row.field.replace(/_/g, ' ')}` : ''}: </span>
            <Description row={row} />
          </li>
        ))}
      </ul>
      {rows.length > COLLAPSED_ROWS && (
        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          style={{ marginTop: 4, padding: 0, background: 'none', border: 'none', color: ACCENT_TEXT, cursor: 'pointer', fontSize: 12 }}
        >
          {expanded ? 'Show fewer' : `Show all ${rows.length}`}
        </button>
      )}
    </>
  );
}

export function ChangeList({ batchId, fallbackLines, onLoaded }: {
  batchId: string;
  /** The card's bounded summary lines, shown until (or instead of) the fetched rows. */
  fallbackLines?: string[];
  onLoaded?: (loaded: boolean) => void;
}) {
  const [result, setResult] = useState<{ batchId: string; attempt: number; batch: ChangeBatch | null } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [undoing, setUndoing] = useState(false);
  const current = result?.batchId === batchId && result.attempt === attempt ? result : null;
  const batch = current?.batch;

  useEffect(() => {
    let live = true;
    onLoaded?.(false);
    api<ChangeBatch>(`${URL}/${batchId}`)
      .then((b) => {
        if (live) {
          setResult({ batchId, attempt, batch: b });
          onLoaded?.(true);
        }
      })
      .catch(() => { if (live) setResult({ batchId, attempt, batch: null }); });
    return () => { live = false; };
  }, [batchId, attempt, onLoaded]);

  if (!batch) {
    return (
      <div style={{ fontSize: 12, color: INK_MUTE, marginBottom: 10 }}>
        <div role="status">{current ? 'Could not load the change list.' : 'Loading the change list…'}</div>
        {current && <button type="button" onClick={() => setAttempt(n => n + 1)}>Retry</button>}
        {!!fallbackLines?.length && <ul style={{ paddingLeft: 20 }}>
          {fallbackLines.map((line, i) => <li key={i}>{line}</li>)}
        </ul>}
      </div>
    );
  }

  const count = (state: ChangeRow['state']) => batch.rows.filter((r) => r.state === state).length;
  const changes = batch.rows.filter((r) => !NOT_CHANGED.has(r.state));
  const left = batch.rows.filter((r) => NOT_CHANGED.has(r.state));
  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const heading = batch.status === 'proposed' ? plural(changes.length, 'change')
    : batch.status === 'applied' ? `Applied ${plural(count('applied'), 'change')}`
    : `Undone: ${count('undone')} restored${count('kept') ? `, ${count('kept')} left as edited since` : ''}`;

  const undo = async () => {
    setUndoing(true);
    try {
      const undone = await api<ChangeBatch>(`${URL}/${batchId}/undo`, { method: 'POST' });
      setResult({ batchId, attempt, batch: undone });
    } catch (e) {
      toast.error(e instanceof ApiError ? e.detail : 'Could not undo that change list.');
    } finally {
      setUndoing(false);
    }
  };

  return (
    <div data-testid="change-list" style={{ fontSize: 12, marginBottom: 10 }}>
      {batch.reason && <div style={{ color: INK_MUTE, marginBottom: 4 }}>Why: {batch.reason}</div>}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: INK_SOFT, marginBottom: 4 }}>
        <span>{heading}</span>
        {batch.status === 'applied' && count('applied') > 0 && (
          <button
            type="button"
            onClick={undo}
            disabled={undoing}
            style={{
              marginLeft: 'auto', padding: '3px 10px', fontSize: 12, background: 'none', color: INK,
              border: `1px solid ${LINE}`, borderRadius: 7, cursor: undoing ? 'default' : 'pointer',
            }}
          >
            {undoing ? 'Undoing…' : 'Undo'}
          </button>
        )}
      </div>
      {changes.length > 0 && <Rows rows={changes} />}
      {left.length > 0 && (
        <div data-testid="change-list-refused" style={{ marginTop: 8, padding: 8, border: `1px solid ${LINE}`, borderRadius: 8 }}>
          <div style={{ color: INK, fontWeight: 600, marginBottom: 4 }}>
            {batch.status === 'proposed' ? 'Will not be changed' : 'Not changed'} ({left.length})
          </div>
          <Rows rows={left} />
        </div>
      )}
    </div>
  );
}
