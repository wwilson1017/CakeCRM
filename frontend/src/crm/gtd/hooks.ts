// Todo-GTD — shared page plumbing.
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from '../../shared/toast';
import { updateTodo } from './api';
import type { Todo, TodoStatus } from './types';
import { refreshMeta } from './useTodoMeta';

/**
 * Load todos for a page. `refetchKey` re-runs the fetch when a page-level input
 * changes (e.g. the Done page's done|dropped toggle); reload() re-runs it after a
 * mutation. Unmounted or superseded responses are dropped.
 */
export function useTodos(fetcher: () => Promise<Todo[]>, refetchKey: unknown = null) {
  const [todos, setTodos] = useState<Todo[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [loadSeq, setLoadSeq] = useState(0);
  const fetcherRef = useRef(fetcher);

  useEffect(() => { fetcherRef.current = fetcher; });

  useEffect(() => {
    let cancelled = false;
    fetcherRef.current()
      .then(result => {
        if (cancelled) return;
        setTodos(result);
        setFailed(false);
      })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [loadSeq, refetchKey]);

  const reload = useCallback(() => { setLoadSeq(s => s + 1); }, []);
  return { todos, failed, reload };
}

/**
 * Row mutations every list page shares. Each mutation reloads the page's list AND the
 * shared meta, so the inbox badge and status counts stay honest. Failures surface as
 * a toast — one-click row actions have no inline error slot.
 */
export function useRowActions(reload: () => void) {
  const after = useCallback(() => {
    reload();
    void refreshMeta();
  }, [reload]);

  const patch = useCallback(async (id: number, fields: Record<string, unknown>) => {
    try {
      await updateTodo(id, fields);
      return true;
    } catch {
      toast.error('Failed to update todo.');
      return false;
    }
  }, []);

  const toggleDone = useCallback(async (todo: Todo, uncheckStatus: TodoStatus = 'next_action') => {
    const reopeningSpawned = todo.status === 'done' && !!todo.repeat;
    const ok = await patch(todo.id, {
      status: todo.status === 'done' ? uncheckStatus : 'done',
    });
    if (ok && reopeningSpawned) {
      // Completing a repeating todo already spawned its successor, so reopening this
      // one leaves both copies live. Say so rather than let it look like a bug.
      toast.info('Reopened — its next occurrence already exists from when it was completed.');
    }
    after();
  }, [after, patch]);

  const toggleStar = useCallback(async (todo: Todo) => {
    await patch(todo.id, { star: !todo.star });
    after();
  }, [after, patch]);

  const setStatus = useCallback(async (todo: Todo, status: TodoStatus) => {
    await patch(todo.id, { status });
    after();
  }, [after, patch]);

  return { toggleDone, toggleStar, setStatus, after };
}
