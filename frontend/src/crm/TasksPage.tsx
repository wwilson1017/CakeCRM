/**
 * Tasks (normal mode), on the shared collection layer (issue #77).
 *
 * Designed rather than ported: the issue names a `TasksTab.tsx` in the blueprint, but no
 * such file exists — that CRM has four tabs (Dashboard/Contacts/Companies/Pipeline) and
 * keeps tasks in a separate todo app that never adopted this layer. So the wiring follows
 * ContactsTab and the domain is this repo's own.
 *
 * This page gains free-text search, which it has never had, and loses the 100-row silent
 * truncation. Unlike Contacts and Companies it DOES use the layer's `CollectionDetail`
 * shell: a task has no route to preserve, and its detail was already a modal covering the
 * assistant launcher, so the z-index objection that kept those two on routed pages does
 * not apply here.
 *
 * GTD mode is unaffected — `TasksModeRouter` still chooses between this page and the GTD
 * surfaces, and this export's name is unchanged.
 */
import { useCallback, useMemo, useState } from 'react';
import { api } from '../core/api/client';
import type { CrmTask } from '../core/types';
import { TaskForm } from './components/TaskForm';
import { PriorityBadge } from './components/badges';
import { RefreshButton } from './components/RefreshButton';
import { useOwnerOptions } from './useOwnerOptions';
import { CollectionView, useCollectionState } from '../shared/collection';
import type { FacetOption } from '../shared/search';
import { IconPlus } from '../shared/icons';
import { useIsMobile } from '../shared/useIsMobile';
import { toast } from '../shared/toast';
import {
  INK, INK_MUTE, INK_DIM, LINE_STRONG, CORAL, SAGE, ACCENT_INK, HOVER, mono,
} from '../shared/styles';
import { pageHeading, btnPrimary, btnSecondary, btnSmall } from './styles';
import { makeTasksCollectionConfig } from './collectionConfig';
import { buildTaskColumns, buildDoneFacetRenderers } from './listColumns';
import { useCrmCorpus, rowIsGone, writeMayHaveLanded, type CrmCorpus } from './usePatchableAssembly';
import { useLocalDay } from './useLocalDay';

import { dueLabel } from './gtd/util';

const NO_ROWS: CrmTask[] = [];
const DONE_FACET = buildDoneFacetRenderers();

export function TasksPage() {
  const isMobile = useIsMobile();
  const [showCreate, setShowCreate] = useState(false);
  const [editTask, setEditTask] = useState<CrmTask | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const { options: owners, loading: usersLoading } = useOwnerOptions();
  const { today, now } = useLocalDay();

  const corpus = useCrmCorpus<CrmTask>(
    useCallback(async (params, signal) => {
      // No `completed=` filter: the corpus is every live, non-dropped task, and which of
      // them to show is the Done facet's business, client-side.
      const res = await api<{ tasks: CrmTask[] }>(`/api/crm/tasks?${params}`, { signal });
      return res.tasks;
    }, []),
  );
  const { upsert, remove, retry } = corpus;

  const toggleComplete = useCallback(async (task: CrmTask) => {
    let saved: CrmTask;
    try {
      saved = task.completed
        ? await api<CrmTask>(`/api/crm/tasks/${task.id}`, { method: 'PUT', body: JSON.stringify({ completed: 0 }) })
        : await api<CrmTask>(`/api/crm/tasks/${task.id}/complete`, { method: 'PUT' });
    } catch (err) {
      toast.error('Failed to update task.');
      // A 404 says someone else already deleted it, so drop the ghost rather than keep
      // failing on it. Any other 4xx wrote nothing and the list is still right; anything
      // else may have committed and lost the response, so re-sweep.
      if (rowIsGone(err)) remove(task.id);
      else if (writeMayHaveLanded(err)) retry();
      return;
    }
    // Completing a REPEATING task spawns its next occurrence server-side (#70) — a row no
    // local patch can invent. Decided from the SERVER's copy, not the pre-write one: the
    // response reflects the state it actually used to decide whether to spawn.
    if (!task.completed && saved.repeat) retry();
    else upsert(saved);
  }, [upsert, remove, retry]);

  const columns = useMemo(() => buildTaskColumns(toggleComplete, today), [toggleComplete, today]);
  return (
    <div style={{ padding: isMobile ? '20px 16px' : '32px 44px', maxWidth: 900 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: isMobile ? 16 : 24 }}>
        <h1 style={pageHeading(isMobile)}>Tasks</h1>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <RefreshButton onClick={retry} label="Reload tasks" />
          <button onClick={() => setShowCreate(true)} style={{ ...btnPrimary, ...btnSmall }}>
            <IconPlus size={13} strokeWidth={2.25} /> {isMobile ? 'Add' : 'Add Task'}
          </button>
        </div>
      </div>

      {usersLoading
        ? <p style={{ ...mono(12), color: INK_DIM }}>Loading…</p>
        : (
          <TasksCollection
            corpus={corpus}
            columns={columns}
            owners={owners}
            now={now}
            today={today}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onEdit={task => { setSelectedId(null); setEditTask(task); }}
            onToggleComplete={async task => { await toggleComplete(task); setSelectedId(null); }}
          />
        )}

      {showCreate && (
        <TaskForm
          onClose={() => setShowCreate(false)}
          onSaved={saved => { setShowCreate(false); upsert(saved); }}
          onWriteUncertain={retry}
        />
      )}
      {editTask && (
        <TaskForm
          task={editTask}
          onClose={() => setEditTask(null)}
          onSaved={saved => { setEditTask(null); upsert(saved); }}
          onWriteUncertain={retry}
        />
      )}
    </div>
  );
}

interface CollectionProps {
  corpus: CrmCorpus<CrmTask>;
  columns: ReturnType<typeof buildTaskColumns>;
  owners: FacetOption[] | null;
  now: Date;
  today: string;
  selectedId: number | null;
  onSelect: (id: number | null) => void;
  onEdit: (task: CrmTask) => void;
  onToggleComplete: (task: CrmTask) => void | Promise<void>;
}

function TasksCollection(
  { corpus, columns, owners, now, today, selectedId, onSelect, onEdit, onToggleComplete }: CollectionProps,
) {
  const config = useMemo(
    () => makeTasksCollectionConfig({ columns, owners, doneFacet: DONE_FACET, now }),
    [columns, owners, now],
  );
  const rows = corpus.items ?? NO_ROWS;
  const state = useCollectionState(config, rows);
  return (
    <CollectionView<CrmTask>
      config={config}
      state={state}
      items={rows}
      selectedId={selectedId}
      onSelect={id => onSelect(id === null ? null : Number(id))}
      searchPlaceholder="Search tasks..."
      loading={{
        loading: corpus.loading,
        error: corpus.error,
        itemsLoaded: corpus.itemsLoaded,
        retry: corpus.retry,
      }}
      detail={{
        render: task => (
          <TaskDetailBody
            task={task}
            onEdit={() => onEdit(task)}
            onToggleComplete={() => onToggleComplete(task)}
            today={today}
          />
        ),
        // Escape and backdrop are allowed to close, unlike the CRM's routed panels. That
        // policy exists to protect nested modals with no Escape handling of their own;
        // this body opens TaskForm only AFTER closing itself, so none can be orphaned.
        onRequestClose: () => true,
      }}
    />
  );
}

/** The task detail — title and subtitle come from `config.detail`, so this is the body only. */
function TaskDetailBody(
  { task, onEdit, onToggleComplete, today }:
  { task: CrmTask; onEdit: () => void; onToggleComplete: () => void | Promise<void>; today: string },
) {
  const due = task.due_date ? dueLabel(task.due_date, today) : null;
  const late = !!due?.overdue && !task.completed;
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
        <PriorityBadge priority={task.priority} />
      </div>

      {task.description && (
        <p style={{ fontSize: 14, color: INK_MUTE, marginBottom: 16, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>
          {task.description}
        </p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 20 }}>
        {task.contact_name && <DetailRow label="Contact" value={task.contact_name} />}
        {task.deal_title && <DetailRow label="Deal" value={task.deal_title} />}
        {due && (
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ ...mono(10), color: INK_DIM }}>Due</span>
            <span style={{ fontSize: 13, color: late ? CORAL : INK, fontWeight: late ? 600 : 400 }}>
              {due.text}
            </span>
          </div>
        )}
        {!!task.completed && <DetailRow label="Status" value="Completed" />}
      </div>

      <div style={{ display: 'flex', gap: 8 }}>
        <button onClick={onEdit} style={{ ...btnSecondary, padding: '10px 16px', borderRadius: 6, fontSize: 13, border: `1px solid ${LINE_STRONG}`, color: INK }}>
          Edit
        </button>
        <button
          onClick={() => { void onToggleComplete(); }}
          style={{
            flex: 1, padding: '10px 16px', borderRadius: 6,
            background: task.completed ? HOVER : SAGE,
            color: task.completed ? INK : ACCENT_INK,
            border: 'none', fontWeight: 500, fontSize: 13, cursor: 'pointer',
          }}
        >
          {task.completed ? 'Mark Incomplete' : 'Mark Complete'}
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
