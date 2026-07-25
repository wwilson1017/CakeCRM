import { useState, useRef, useEffect, useCallback } from 'react';
import type { KanbanItem, MoveEvent } from './types';

type ItemMap<T> = Record<string, T[]>;

function cloneItems<T>(items: ItemMap<T>): ItemMap<T> {
  const result: ItemMap<T> = {};
  for (const key of Object.keys(items)) {
    result[key] = [...items[key]];
  }
  return result;
}

function toStringKeys<T extends KanbanItem>(items: Record<string | number, T[]>): ItemMap<T> {
  const result: ItemMap<T> = {};
  for (const key of Object.keys(items)) {
    result[String(key)] = [...items[key]];
  }
  return result;
}

export default function useKanbanState<TItem extends KanbanItem>(
  externalItems: Record<string | number, TItem[]>,
) {
  const [items, setItems] = useState<ItemMap<TItem>>(() => toStringKeys(externalItems));
  const snapshotRef = useRef<ItemMap<TItem> | null>(null);
  const draggingRef = useRef(false);
  // Always-current mirror of externalItems (updated even mid-drag), so a canceled
  // gesture can re-sync to the latest data instead of a stale pre-drag snapshot.
  const externalRef = useRef(externalItems);

  useEffect(() => {
    externalRef.current = externalItems;
    if (!draggingRef.current) {
      setItems(toStringKeys(externalItems));
    }
  }, [externalItems]);

  const startDrag = useCallback(() => {
    draggingRef.current = true;
    snapshotRef.current = cloneItems(items);
  }, [items]);

  const moveItem = useCallback((itemId: string | number, fromCol: string | number, toCol: string | number, newIndex: number) => {
    setItems(prev => {
      const next = cloneItems(prev);
      const fromKey = String(fromCol);
      const toKey = String(toCol);
      const fromList = next[fromKey] || [];
      const idx = fromList.findIndex(i => String(i.id) === String(itemId));
      if (idx === -1) return prev;

      const [item] = fromList.splice(idx, 1);
      next[fromKey] = fromList;

      const toList = next[toKey] || [];
      const pos = Math.max(0, Math.min(newIndex, toList.length));
      toList.splice(pos, 0, item);
      next[toKey] = toList;

      return next;
    });
  }, []);

  const commitMove = useCallback(async (
    onMove: (event: MoveEvent<TItem>) => Promise<void>,
    event: MoveEvent<TItem>,
  ) => {
    try {
      await onMove(event);
    } catch (err) {
      console.error('Kanban move failed:', err);
      if (snapshotRef.current) {
        setItems(snapshotRef.current);
      }
    } finally {
      draggingRef.current = false;
      snapshotRef.current = null;
    }
  }, []);

  const rollback = useCallback(() => {
    // Re-sync to the LATEST external items rather than the pre-drag snapshot: while
    // this gesture was active an out-of-band update (e.g. another card's failed
    // move reverting in `data`) may have landed but been skipped by the resync
    // effect above, and restoring the stale snapshot would silently drop it.
    setItems(toStringKeys(externalRef.current));
    draggingRef.current = false;
    snapshotRef.current = null;
  }, []);

  const findItem = useCallback((itemId: string | number): TItem | undefined => {
    const sid = String(itemId);
    for (const col of Object.values(items)) {
      const found = col.find(i => String(i.id) === sid);
      if (found) return found;
    }
    return undefined;
  }, [items]);

  const findColumnForItem = useCallback((itemId: string | number): string | undefined => {
    const sid = String(itemId);
    for (const [colId, col] of Object.entries(items)) {
      if (col.some(i => String(i.id) === sid)) return colId;
    }
    return undefined;
  }, [items]);

  return { items, startDrag, moveItem, commitMove, rollback, findItem, findColumnForItem };
}
