/**
 * Todos (normal mode), on the shared collection layer (issue #77).
 *
 * Designed rather than ported: the issue names a `TodosTab.tsx` in the blueprint, but no
 * such file exists — that CRM has four tabs (Dashboard/Contacts/Companies/Pipeline) and
 * keeps todos in a separate todo app that never adopted this layer. So the wiring follows
 * ContactsTab and the domain is this repo's own.
 *
 * This page gains free-text search, which it has never had, and loses the 100-row silent
 * truncation. Unlike Contacts and Companies it DOES use the layer's `CollectionDetail`
 * shell: a todo has no route to preserve, and its detail was already a modal covering the
 * assistant launcher, so the z-index objection that kept those two on routed pages does
 * not apply here.
 *
 * GTD mode is unaffected — `TodosModeRouter` still chooses between this page and the GTD
 * surfaces, and this export's name is unchanged.
 */
import { useCallback, useMemo, useState } from 'react';
import { api } from '../core/api/client';
import type { CrmTodo } from '../core/types';
import { TodoForm } from './components/TodoForm';
import { PriorityBadge } from './components/badges';
import { RefreshButton } from './components/RefreshButton';
import { useOwnerOptions } from './useOwnerOptions';
import { CollectionView, useCollectionState } from '../shared/collection';
import type { FacetOption } from '../shared/search';
import { IconPlus } from '../shared/icons';
import { useIsMobile } from '../shared/useIsMobile';
import { toast } from '../shared/toast';
import {
  INK, INK_MUTE, INK_DIM, LINE_STRONG, CORAL_TEXT, SAGE_FILL, ON_STATUS, HOVER, mono,
} from '../shared/styles';
import { pageHeading, btnPrimary, btnSecondary, btnSmall } from './styles';
import { makeTodosCollectionConfig } from './collectionConfig';
import { buildTodoColumns, buildDoneFacetRenderers } from './listColumns';
import { useCrmCorpus, rowIsGone, writeMayHaveLanded, type CrmCorpus } from './usePatchableAssembly';
import { useLocalDay } from './useLocalDay';

import { dueLabel } from './gtd/util';

const NO_ROWS: CrmTodo[] = [];
const DONE_FACET = buildDoneFacetRenderers();

export function TodosPage() {
  const isMobile = useIsMobile();
  const [showCreate, setShowCreate] = useState(false);
  const [editTodo, setEditTodo] = useState<CrmTodo | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const { options: owners, loading: usersLoading } = useOwnerOptions();
  const { today, now } = useLocalDay();

  const corpus = useCrmCorpus<CrmTodo>(
    useCallback(async (params, signal) => {
      // No `completed=` filter: the corpus is every live, non-dropped todo, and which of
      // them to show is the Done facet's business, client-side.
      const res = await api<{ todos: CrmTodo[] }>(`/api/crm/todos?${params}`, { signal });
      return res.todos;
    }, []),
  );
  const { upsert, remove, retry } = corpus;

  const toggleComplete = useCallback(async (todo: CrmTodo) => {
    let saved: CrmTodo;
    try {
      saved = todo.completed
        ? await api<CrmTodo>(`/api/crm/todos/${todo.id}`, { method: 'PUT', body: JSON.stringify({ completed: 0 }) })
        : await api<CrmTodo>(`/api/crm/todos/${todo.id}/complete`, { method: 'PUT' });
    } catch (err) {
      toast.error('Failed to update todo.');
      // A 404 says someone else already deleted it, so drop the ghost rather than keep
      // failing on it. Any other 4xx wrote nothing and the list is still right; anything
      // else may have committed and lost the response, so re-sweep.
      if (rowIsGone(err)) remove(todo.id);
      else if (writeMayHaveLanded(err)) retry();
      return;
    }
    // Completing a REPEATING todo spawns its next occurrence server-side (#70) — a row no
    // local patch can invent. Decided from the SERVER's copy, not the pre-write one: the
    // response reflects the state it actually used to decide whether to spawn.
    if (!todo.completed && saved.repeat) retry();
    else upsert(saved);
  }, [upsert, remove, retry]);

  const columns = useMemo(() => buildTodoColumns(toggleComplete, today), [toggleComplete, today]);
  return (
    <div style={{ padding: isMobile ? '20px 16px' : '32px 44px', maxWidth: 900 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: isMobile ? 16 : 24 }}>
        <h1 style={pageHeading(isMobile)}>Todos</h1>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <RefreshButton onClick={retry} label="Reload todos" />
          <button onClick={() => setShowCreate(true)} style={{ ...btnPrimary, ...btnSmall }}>
            <IconPlus size={13} strokeWidth={2.25} /> {isMobile ? 'Add' : 'Add Todo'}
          </button>
        </div>
      </div>

      {usersLoading
        ? <p style={{ ...mono(12), color: INK_DIM }}>Loading…</p>
        : (
          <TodosCollection
            corpus={corpus}
            columns={columns}
            owners={owners}
            now={now}
            today={today}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onEdit={todo => { setSelectedId(null); setEditTodo(todo); }}
            onToggleComplete={async todo => { await toggleComplete(todo); setSelectedId(null); }}
          />
        )}

      {showCreate && (
        <TodoForm
          onClose={() => setShowCreate(false)}
          onSaved={saved => { setShowCreate(false); upsert(saved); }}
          onWriteUncertain={retry}
        />
      )}
      {editTodo && (
        <TodoForm
          todo={editTodo}
          onClose={() => setEditTodo(null)}
          onSaved={saved => { setEditTodo(null); upsert(saved); }}
          onWriteUncertain={retry}
        />
      )}
    </div>
  );
}

interface CollectionProps {
  corpus: CrmCorpus<CrmTodo>;
  columns: ReturnType<typeof buildTodoColumns>;
  owners: FacetOption[] | null;
  now: Date;
  today: string;
  selectedId: number | null;
  onSelect: (id: number | null) => void;
  onEdit: (todo: CrmTodo) => void;
  onToggleComplete: (todo: CrmTodo) => void | Promise<void>;
}

function TodosCollection(
  { corpus, columns, owners, now, today, selectedId, onSelect, onEdit, onToggleComplete }: CollectionProps,
) {
  const config = useMemo(
    () => makeTodosCollectionConfig({ columns, owners, doneFacet: DONE_FACET, now }),
    [columns, owners, now],
  );
  const rows = corpus.items ?? NO_ROWS;
  const state = useCollectionState(config, rows);
  return (
    <CollectionView<CrmTodo>
      config={config}
      state={state}
      items={rows}
      selectedId={selectedId}
      onSelect={id => onSelect(id === null ? null : Number(id))}
      searchPlaceholder="Search todos..."
      loading={{
        loading: corpus.loading,
        error: corpus.error,
        itemsLoaded: corpus.itemsLoaded,
        retry: corpus.retry,
      }}
      detail={{
        render: todo => (
          <TodoDetailBody
            todo={todo}
            onEdit={() => onEdit(todo)}
            onToggleComplete={() => onToggleComplete(todo)}
            today={today}
          />
        ),
        // Escape and backdrop are allowed to close, unlike the CRM's routed panels. That
        // policy exists to protect nested modals with no Escape handling of their own;
        // this body opens TodoForm only AFTER closing itself, so none can be orphaned.
        onRequestClose: () => true,
      }}
    />
  );
}

/** The todo detail — title and subtitle come from `config.detail`, so this is the body only. */
function TodoDetailBody(
  { todo, onEdit, onToggleComplete, today }:
  { todo: CrmTodo; onEdit: () => void; onToggleComplete: () => void | Promise<void>; today: string },
) {
  const due = todo.due_date ? dueLabel(todo.due_date, today) : null;
  const late = !!due?.overdue && !todo.completed;
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
        <PriorityBadge priority={todo.priority} />
      </div>

      {todo.description && (
        <p style={{ fontSize: 14, color: INK_MUTE, marginBottom: 16, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>
          {todo.description}
        </p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 20 }}>
        {todo.contact_name && <DetailRow label="Contact" value={todo.contact_name} />}
        {todo.deal_title && <DetailRow label="Deal" value={todo.deal_title} />}
        {due && (
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ ...mono(10), color: INK_DIM }}>Due</span>
            <span style={{ fontSize: 13, color: late ? CORAL_TEXT : INK, fontWeight: late ? 600 : 400 }}>
              {due.text}
            </span>
          </div>
        )}
        {!!todo.completed && <DetailRow label="Status" value="Completed" />}
      </div>

      <div style={{ display: 'flex', gap: 8 }}>
        <button onClick={onEdit} style={{ ...btnSecondary, padding: '10px 16px', borderRadius: 6, fontSize: 13, border: `1px solid ${LINE_STRONG}`, color: INK }}>
          Edit
        </button>
        <button
          onClick={() => { void onToggleComplete(); }}
          style={{
            flex: 1, padding: '10px 16px', borderRadius: 6,
            background: todo.completed ? HOVER : SAGE_FILL,
            color: todo.completed ? INK : ON_STATUS,
            border: 'none', fontWeight: 500, fontSize: 13, cursor: 'pointer',
          }}
        >
          {todo.completed ? 'Mark Incomplete' : 'Mark Complete'}
        </button>
      </div>
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
      <span style={{ ...mono(10), color: INK_DIM }}>{label}</span>
      <span style={{ fontSize: 13, color: INK }}>{value}</span>
    </div>
  );
}
