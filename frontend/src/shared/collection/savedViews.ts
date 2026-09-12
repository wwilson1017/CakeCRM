/**
 * Saved views client (issue #181) — the wire types, the snapshot builder, the staleness rule
 * and four thin `api()` calls. Pure + transport only; the UI lives in `SavedViewsMenu`.
 *
 * This is the collection layer's first network call. It is `core/api/client`'s `api()`, the
 * one authenticated JSON client (`shared/MobileMenuDrawer` reaching into `core/` is the
 * precedent for the direction of the import), and NOT the auth context: `can_edit` and
 * `created_by_name` are computed server-side precisely so this layer needs no notion of who
 * is looking — `useAuth` throws outside an `AuthProvider` and would break every existing
 * `CollectionView` test.
 */
import { api } from '../../core/api/client';
import type { CollectionSnapshot, CollectionState, CollectionStorage } from './types';

export interface SavedView {
  id: number;
  surface: string;
  name: string;
  version: number;
  /** Opaque here — `applySnapshot` owns the coercion. */
  payload: unknown;
  created_by: number | null;
  created_by_name: string | null;
  created_at: string;
  updated_at: string;
  /** Computed for the CALLER by the server: creator or admin. */
  can_edit: boolean;
}

export const SAVED_VIEWS_PATH = '/api/saved-views';

/** The slice of collection state a snapshot reads — no `T`, so the menu stays generic-free. */
export type SnapshotSource = Pick<
  CollectionState<unknown>,
  'query' | 'facetSelections' | 'voided' | 'sort' | 'view'
>;

export function snapshotFromState(s: SnapshotSource): CollectionSnapshot {
  return { query: s.query, facets: s.facetSelections, voided: s.voided, sort: s.sort, view: s.view };
}

/**
 * A view saved under a different version of this surface. The layer refuses to APPLY one:
 * between versions a facet key may have been renamed or repurposed, so the stored selection no
 * longer means what it said, and a filter that silently matches everything is the failure this
 * stamp exists to prevent. Such a view is shown disabled with a reason, never hidden — an
 * invisible row can never be repaired or cleaned up.
 */
export function isStaleView(view: SavedView, storage: CollectionStorage): boolean {
  return view.version !== storage.version;
}

export function listSavedViews(surface: string): Promise<SavedView[]> {
  return api<{ views: SavedView[] }>(
    `${SAVED_VIEWS_PATH}?surface=${encodeURIComponent(surface)}`,
    // A response body that is not the envelope this asks for yields an empty list rather than
    // `undefined` — the layer's rule that no payload off the wire may white-screen a page
    // applies to the response shape too, not only to a saved view's contents.
  ).then(r => (Array.isArray(r?.views) ? r.views : []));
}

export function createSavedView(body: {
  surface: string;
  name: string;
  version: number;
  payload: CollectionSnapshot;
}): Promise<SavedView> {
  return api<SavedView>(SAVED_VIEWS_PATH, { method: 'POST', body: JSON.stringify(body) });
}

export function updateSavedView(
  id: number,
  body: { name?: string; version?: number; payload?: CollectionSnapshot },
): Promise<SavedView> {
  return api<SavedView>(`${SAVED_VIEWS_PATH}/${id}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
}

export function deleteSavedView(id: number): Promise<{ ok: true }> {
  return api<{ ok: true }>(`${SAVED_VIEWS_PATH}/${id}`, { method: 'DELETE' });
}
