/**
 * The company's merged notes + activity feed (issue #144).
 *
 * Not `NotesThread`: that component is bound to ONE entity and carries write affordances
 * (compose, edit, archive) that only make sense with a single parent. This feed has many
 * parents and is read-only, so every row instead carries a source chip saying which deal or
 * contact it came from — information a single-entity thread has no place to put.
 *
 * Two rules here are load-bearing and easy to get wrong:
 *
 * 1. **Rows are keyed `(source, id)`, never `id`.** Notes and activities come from two
 *    tables with independent id sequences, so note #7 and activity #7 are different rows.
 * 2. **The paging offset advances by the rows the SERVER returned**, never by the length of
 *    the deduped list. A page that turns out to be entirely rows we already hold must still
 *    move the cursor past it, which a list-length offset cannot do — it would re-request the
 *    same page forever.
 *
 * Every request carries a generation token. Without one, toggling the archived filter while
 * a "load more" is in flight appends old-filter rows to the new feed, and a slow first page
 * can land on top of a newer one.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../core/api/client';
import type { CrmTimelineEntry, CrmTimelinePage } from '../../core/types';
import {
  appendTimelinePage, describeTimelineEntry, entryKey, groupTimelineByDate, timelineSourceLabel,
} from '../companyRollup';
import { useUsers } from '../useUsers';
import { formatDate } from '../../shared/formatDate';
import {
  ACCENT_TEXT, INK, INK_DIM, INK_MUTE, LINE, LINE_STRONG, FONT_DISPLAY, mono,
} from '../../shared/styles';
import { btnSmall } from '../styles';

const PAGE = 100;

interface Props {
  companyId: number;
  includeArchived: boolean;
}

interface FeedState {
  /** The request this state belongs to: companyId, filter and retry. */
  key: string;
  entries: CrmTimelineEntry[];
  serverOffset: number;
  hasMore: boolean;
  failed: boolean;
  loadingMore: boolean;
}

function SourceChip({ entry }: { entry: CrmTimelineEntry }) {
  const source = timelineSourceLabel(entry);
  const label = `${source.kind} · ${source.name}${source.archived ? ' (archived)' : ''}`;
  const style = {
    fontSize: 12,
    color: source.archived ? INK_DIM : ACCENT_TEXT,
    textDecoration: source.archived ? ('line-through' as const) : undefined,
  };
  // An archived source stays a LINK: `GET /api/crm/deals/:id` has no live filter and the
  // detail surfaces render an archived record so it can be restored. Withholding the link
  // would strand exactly the record a reader most needs to reach.
  if (entry.entity_type === 'company') {
    return <Link to={`/crm/companies/${entry.entity_id}`} style={style}>{label}</Link>;
  }
  if (entry.entity_type === 'contact') {
    return <Link to={`/crm/contacts/${entry.entity_id}`} style={style}>{label}</Link>;
  }
  return <span style={style}>{label}</span>;
}

function Entry({ entry, actor }: { entry: CrmTimelineEntry; actor: string | null }) {
  return (
    <div style={{
      borderLeft: `2px solid ${entry.source === 'note' ? LINE_STRONG : LINE}`,
      paddingLeft: 12, margin: '10px 0',
    }}>
      <div style={{ fontSize: 13, color: INK, whiteSpace: 'pre-wrap' }}>
        {describeTimelineEntry(entry)}
      </div>
      <div style={{
        display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'baseline', marginTop: 4,
      }}>
        <SourceChip entry={entry} />
        <span style={{ fontSize: 12, color: INK_DIM }}>{formatDate(entry.created_at)}</span>
        {actor && <span style={{ fontSize: 12, color: INK_DIM }}>· {actor}</span>}
        {entry.updated_at && <span style={{ fontSize: 12, color: INK_DIM }}>· edited</span>}
        {entry.archived === 1 && <span style={{ fontSize: 12, color: INK_DIM }}>· archived</span>}
        {entry.attachments && entry.attachments.length > 0 && (
          <span style={{ fontSize: 12, color: INK_DIM }}>
            · {entry.attachments.length} attachment{entry.attachments.length === 1 ? '' : 's'}
          </span>
        )}
      </div>
    </div>
  );
}

export function CompanyTimeline({ companyId, includeArchived }: Props) {
  const { nameFor } = useUsers();
  const [retryNonce, setRetryNonce] = useState(0);

  // The feed is ONE state object tagged with the request it belongs to, and everything
  // rendered is derived from it during render. The obvious shape — six useStates and an
  // effect that resets them all when the filter changes — is a cascading render the
  // stricter react-hooks ruleset rejects outright, and it is also a real bug surface: the
  // reset and the fetch are two steps, so a response from the PREVIOUS filter can land in
  // between. Tagging the state answers both at once — a stale response simply is not the
  // current key, so it can never be shown.
  const key = `${companyId}:${includeArchived}:${retryNonce}`;
  const [feed, setFeed] = useState<FeedState | null>(null);
  const current = feed && feed.key === key ? feed : null;

  // Still needed alongside the key: two load-more clicks share one key, so the second must
  // be refused on identity, not on the filter.
  const genRef = useRef(0);

  const path = useCallback(
    (offset: number) =>
      `/api/crm/companies/${companyId}/timeline?limit=${PAGE}&offset=${offset}`
      + (includeArchived ? '&include_archived=true' : ''),
    [companyId, includeArchived],
  );

  useEffect(() => {
    const gen = ++genRef.current;
    api<CrmTimelinePage>(path(0)).then(
      page => {
        if (gen !== genRef.current) return;
        setFeed({
          key, entries: page.entries, serverOffset: page.entries.length,
          hasMore: page.has_more, failed: false, loadingMore: false,
        });
      },
      () => {
        if (gen !== genRef.current) return;
        setFeed({
          key, entries: [], serverOffset: 0, hasMore: false,
          failed: true, loadingMore: false,
        });
      },
    );
  }, [path, key]);

  const loadMore = () => {
    if (!current || current.loadingMore) return;
    const gen = genRef.current;
    const from = current.serverOffset;
    setFeed({ ...current, loadingMore: true });
    api<CrmTimelinePage>(path(from)).then(
      page => {
        if (gen !== genRef.current) return;
        setFeed(prev => (prev && prev.key === key ? {
          ...prev,
          entries: appendTimelinePage(prev.entries, page.entries),
          // Advances by the rows the SERVER returned — see the file docstring.
          serverOffset: prev.serverOffset + page.entries.length,
          hasMore: page.has_more,
          failed: false,
          loadingMore: false,
        } : prev));
      },
      () => {
        if (gen !== genRef.current) return;
        setFeed(prev => (prev && prev.key === key
          ? { ...prev, failed: true, loadingMore: false } : prev));
      },
    );
  };

  const entries = current?.entries ?? [];
  const groups = groupTimelineByDate(entries);
  // `current === null` means the first page for THIS key has not landed yet — distinct from
  // an empty feed, so the page never claims the account has no history before it knows.
  const loaded = current !== null;
  const failed = current?.failed ?? false;

  return (
    <section style={{ borderTop: `1px solid ${LINE_STRONG}`, paddingTop: 24, marginTop: 24 }}>
      <h2 style={{ fontFamily: FONT_DISPLAY, fontSize: 18, color: INK, margin: 0 }}>Timeline</h2>

      {!loaded && <p style={{ fontSize: 13, color: INK_DIM }}>Loading timeline…</p>}

      {loaded && failed && entries.length === 0 && (
        <p style={{ fontSize: 13, color: INK_MUTE }}>
          Couldn&apos;t load this timeline.{' '}
          <button type="button" style={btnSmall} onClick={() => setRetryNonce(n => n + 1)}>
            Try again
          </button>
        </p>
      )}

      {loaded && !failed && entries.length === 0 && (
        <p style={{ fontSize: 13, color: INK_DIM }}>No notes or activity on this account yet.</p>
      )}

      {groups.map(([day, rows]) => (
        <div key={day} style={{ marginTop: 16 }}>
          <div style={{ ...mono(10), borderBottom: `1px solid ${LINE}`, paddingBottom: 4 }}>
            {day}
          </div>
          {rows.map(entry => (
            <Entry
              key={entryKey(entry)}
              entry={entry}
              actor={entry.actor_id != null ? nameFor(entry.actor_id) : null}
            />
          ))}
        </div>
      ))}

      {failed && entries.length > 0 && (
        <p style={{ fontSize: 13, color: INK_MUTE, marginTop: 12 }}>
          Couldn&apos;t load more of the timeline.
        </p>
      )}

      {current?.hasMore && (
        <button
          type="button"
          style={{ ...btnSmall, marginTop: 12 }}
          disabled={current.loadingMore}
          onClick={loadMore}
        >
          {current.loadingMore ? 'Loading…' : 'Load more'}
        </button>
      )}
    </section>
  );
}
