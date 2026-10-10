// CakeCRM — the item list on a bulk-create Approve card (#284).
//
// `todo_bulk_create` carries up to 500 todos in one call. Rendered as the card's usual
// one-line JSON of args that is unreadable, so the card lists the items instead, the
// first COLLAPSED_ROWS of them until "Show all" is pressed.

import { useState } from 'react';

import { ACCENT_TEXT, INK, INK_SOFT } from '../shared/styles';
import { COLLAPSED_ROWS } from './bulkItems';

function describe(item: unknown): { title: string; detail: string } {
  if (!item || typeof item !== 'object') return { title: String(item ?? ''), detail: '' };
  const o = item as Record<string, unknown>;
  const detail = [o.project, o.context, o.status, o.due_date]
    .filter((v) => typeof v === 'string' && v.trim())
    .join(' · ');
  return { title: typeof o.title === 'string' ? o.title : '(no title)', detail };
}

export function BulkItemList({ items }: { items: unknown[] }) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? items : items.slice(0, COLLAPSED_ROWS);
  return (
    <div style={{ fontSize: 12, marginBottom: 10 }}>
      <div style={{ color: INK_SOFT, marginBottom: 4 }}>
        {items.length} {items.length === 1 ? 'item' : 'items'}
      </div>
      <ol style={{ margin: 0, paddingLeft: 20, color: INK }}>
        {shown.map((item, i) => {
          const { title, detail } = describe(item);
          return (
            <li key={i}>
              {title}
              {detail && <span style={{ color: INK_SOFT }}> · {detail}</span>}
            </li>
          );
        })}
      </ol>
      {items.length > COLLAPSED_ROWS && (
        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          style={{ marginTop: 4, padding: 0, background: 'none', border: 'none', color: ACCENT_TEXT, cursor: 'pointer', fontSize: 12 }}
        >
          {expanded ? 'Show fewer' : `Show all ${items.length}`}
        </button>
      )}
    </div>
  );
}
