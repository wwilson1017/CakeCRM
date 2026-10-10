/**
 * The freshness line above a warm-cached list page (#281): rows can be on screen before their
 * refresh has finished, so the page says which it is showing. Renders nothing once fresh rows
 * have replaced the cached ones (`savedAt === null`) — the page's own loading surface is the
 * status for a first, cache-less load.
 */
import { INK_DIM, LINE, mono } from '../../shared/styles';
import { formatSavedAt } from '../warmCache';

export function WarmStatus({ savedAt, failed, onRetry }: {
  /** When the cached rows on screen were fetched, or null when the rows are fresh. */
  savedAt: number | null;
  /** The refresh failed and the rows shown are the older, cached set. */
  failed: boolean;
  onRetry: () => void;
}) {
  if (savedAt === null) return null;
  const when = formatSavedAt(savedAt);
  return (
    <p role="status" style={{ ...mono(12), color: INK_DIM, margin: '0 0 12px', display: 'flex', alignItems: 'center', gap: 8 }}>
      {failed ? `Couldn't refresh — showing saved data from ${when}` : `Refreshing… showing saved data from ${when}`}
      {failed && (
        <button
          type="button"
          onClick={onRetry}
          style={{ ...mono(12), color: INK_DIM, background: 'transparent', border: `1px solid ${LINE}`, borderRadius: 4, padding: '2px 8px', cursor: 'pointer' }}
        >
          Retry
        </button>
      )}
    </p>
  );
}
