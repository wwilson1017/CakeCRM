import { useMemo, useState } from 'react';
import { CollectionView, useCollectionState } from '../../shared/collection';
import { listTodos } from './api';
import { makeSomedayConfig, useStableContexts } from './collectionConfig';
import { TodoEditSheet } from './components/TodoEditSheet';
import { useRowActions, useTodos } from './hooks';
import { isTodoPublicMode } from './publicMode';
import { EmptyState, LoadFailed, LoadingRows, TodoShell } from './TodoShell';
import type { Todo } from './types';
import { useTodoMeta } from './useTodoMeta';

const NO_TODOS: Todo[] = [];

/**
 * Someday / Maybe — the parking lot the weekly review prunes.
 *
 * Search, the context facet and the rows run through the shared collection layer (#234); see
 * `collectionConfig.ts` for why this page moved and Inbox/Next/Waiting did not.
 */
export function SomedayPage() {
  const { todos, failed, reload } = useTodos(() => listTodos({ status: 'someday_maybe', limit: 500 }));
  const { toggleDone, toggleStar, setStatus, after } = useRowActions(reload);
  const { projects, filters } = useTodoMeta();
  const [editTodo, setEditTodo] = useState<Todo | null>(null);
  const contexts = useStableContexts(filters?.contexts);

  // Every dep is a stable identity (`useRowActions` memoizes its actions, `setEditTodo` is a
  // setter, `contexts` is content-keyed), so the config is rebuilt only when one genuinely
  // changes — the layer's referential-stability contract, which its memos key on.
  const config = useMemo(
    () => makeSomedayConfig({
      onToggleDone: toggleDone,
      onToggleStar: toggleStar,
      onEdit: setEditTodo,
      onPromote: t => void setStatus(t, 'next_action'),
    }, contexts),
    [toggleDone, toggleStar, setStatus, contexts],
  );
  const items = todos ?? NO_TODOS;
  const state = useCollectionState(config, items);

  return (
    <TodoShell active="someday" onAdded={reload}>
      {failed && <LoadFailed retry={reload} />}
      {!failed && todos === null && <LoadingRows />}
      {!failed && todos !== null && todos.length === 0 && (
        <EmptyState title="Nothing on the someday list" hint="Ideas you're not committing to yet live here." />
      )}
      {!failed && items.length > 0 && (
        <CollectionView
          config={config}
          state={state}
          items={items}
          searchPlaceholder="Filter…"
          savedViews={!isTodoPublicMode}
        />
      )}
      {editTodo && (
        <TodoEditSheet
          todo={editTodo}
          projects={projects}
          contexts={filters?.contexts ?? []}
          onClose={() => setEditTodo(null)}
          onSaved={after}
        />
      )}
    </TodoShell>
  );
}
