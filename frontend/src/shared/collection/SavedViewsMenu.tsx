/**
 * Saved views (issue #181) — the layer's popover for naming, listing, applying and managing
 * team-visible snapshots of a surface's screen state.
 *
 * It lives in `CollectionView`'s toolbar rather than in any page, so every surface built on
 * this layer gets it with no per-page wiring — including the pipeline, which passes no
 * `toolbarExtras` at all. Views are a FILTER affordance, which is why they sit with the bar
 * and not among `toolbarExtras`' actions.
 *
 * Three rules worth stating:
 *
 *  • **Fetch on open, never on mount.** Four surfaces × one request per page load would buy
 *    nothing; the list is small and other members may have saved a view since. Responses are
 *    tagged with a sequence number so a slow first response cannot land after a later one.
 *  • **Authorization is the server's answer, not a guess.** `can_edit` arrives per row, so this
 *    component needs no auth context and no user list.
 *  • **A stale view is shown, disabled, with the reason.** Hiding it would leave a row nobody
 *    can repair or delete; applying it would be the silently-empty filter the version stamp
 *    exists to prevent. Its creator (or an admin) can overwrite it to the current version.
 */
import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../../core/api/client';
import { confirmDialog } from '../confirm';
import { toast } from '../toast';
import {
  createSavedView,
  deleteSavedView,
  isStaleView,
  listSavedViews,
  snapshotFromState,
  updateSavedView,
  type SavedView,
  type SnapshotSource,
} from './savedViews';
import type { CollectionState, CollectionStorage } from './types';

interface ListState {
  seq: number;
  loading: boolean;
  error: string | null;
  views: SavedView[];
}

type Mode = { kind: 'idle' } | { kind: 'save' } | { kind: 'rename'; id: number; name: string };

const EMPTY_LIST: ListState = { seq: 0, loading: false, error: null, views: [] };

function message(err: unknown, fallback: string): string {
  return err instanceof ApiError && err.detail ? err.detail : fallback;
}

/** The inline name form — a `<form>` in the popover, not a modal: one text field does not
 *  justify an overlay, and Enter-to-submit comes free. Module scope so it never remounts. */
function NameForm({
  initial,
  submitLabel,
  error,
  busy,
  onSubmit,
  onCancel,
}: {
  initial: string;
  submitLabel: string;
  error: string | null;
  busy: boolean;
  onSubmit: (name: string) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initial);
  return (
    <form
      className="flex flex-col gap-1.5 border-b border-line px-3 py-2"
      onSubmit={e => {
        e.preventDefault();
        const trimmed = name.trim();
        if (trimmed) onSubmit(trimmed);
      }}
    >
      <input
        type="text"
        required
        maxLength={80}
        autoFocus
        aria-label="View name"
        placeholder="Name this view"
        value={name}
        onChange={e => setName(e.target.value)}
        className="w-full rounded border border-line bg-cream px-2 py-1 text-xs text-charcoal placeholder:text-muted"
      />
      {error !== null && <p className="text-xs text-red-700">{error}</p>}
      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={busy}
          className="rounded border border-line px-2 py-1 text-xs text-charcoal hover:bg-sand disabled:opacity-50"
        >
          {submitLabel}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded px-2 py-1 text-xs text-muted hover:text-charcoal"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

export default function SavedViewsMenu({
  storage,
  state,
  onApplied,
}: {
  storage: CollectionStorage;
  state: SnapshotSource & Pick<CollectionState<unknown>, 'applySnapshot'>;
  onApplied: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [list, setList] = useState<ListState>(EMPTY_LIST);
  const [mode, setMode] = useState<Mode>({ kind: 'idle' });
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const seqRef = useRef(0);

  // Registered only while open, so a page full of collapsed menus costs no listeners.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrapperRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const load = () => {
    const seq = ++seqRef.current;
    setList(prev => ({ ...prev, seq, loading: true, error: null }));
    listSavedViews(storage.key)
      .then(views => {
        // A response from a superseded request is dropped rather than reset-then-fetched, so a
        // slow first load can never overwrite a fresh one.
        if (seq === seqRef.current) setList({ seq, loading: false, error: null, views });
      })
      .catch(err => {
        if (seq === seqRef.current) {
          setList({ seq, loading: false, error: message(err, 'Couldn’t load saved views.'), views: [] });
        }
      });
  };

  const toggleOpen = () => {
    const next = !open;
    setOpen(next);
    setMode({ kind: 'idle' });
    setFormError(null);
    if (next) load();
  };

  const apply = (view: SavedView) => {
    state.applySnapshot(view.payload);
    onApplied();
    setOpen(false);
  };

  /** Every write shares one shape: guard on `busy`, refresh the list, toast the failure. */
  const run = (work: () => Promise<unknown>, done: string, fallback: string) => {
    if (busy) return;
    setBusy(true);
    setFormError(null);
    work()
      .then(() => {
        toast.success(done);
        setMode({ kind: 'idle' });
        load();
      })
      .catch(err => {
        const text = message(err, fallback);
        if (err instanceof ApiError && err.status === 409) setFormError(text);
        else toast.error(text);
      })
      .finally(() => setBusy(false));
  };

  const save = (name: string) =>
    run(
      () =>
        createSavedView({
          surface: storage.key,
          name,
          version: storage.version,
          payload: snapshotFromState(state),
        }),
      'View saved.',
      'Couldn’t save this view.',
    );

  const rename = (id: number, name: string) =>
    run(() => updateSavedView(id, { name }), 'View renamed.', 'Couldn’t rename this view.');

  const overwrite = async (view: SavedView) => {
    const ok = await confirmDialog({
      title: 'Update saved view',
      message: `Replace "${view.name}" with the filters, search, sort and view mode showing now? Everyone sees this view.`,
      confirmLabel: 'Update',
    });
    if (!ok) return;
    run(
      () =>
        updateSavedView(view.id, {
          payload: snapshotFromState(state),
          version: storage.version,
        }),
      'View updated.',
      'Couldn’t update this view.',
    );
  };

  const remove = async (view: SavedView) => {
    const ok = await confirmDialog({
      title: 'Delete saved view',
      message: `Delete "${view.name}"? Everyone on the team loses it.`,
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    run(() => deleteSavedView(view.id), 'View deleted.', 'Couldn’t delete this view.');
  };

  const actionClass = 'rounded px-1.5 py-0.5 text-xs text-muted hover:bg-sand hover:text-charcoal disabled:opacity-50';

  return (
    <div
      ref={wrapperRef}
      className="relative"
      onKeyDown={e => {
        if (e.key === 'Escape') setOpen(false);
      }}
    >
      <button
        type="button"
        onClick={toggleOpen}
        aria-haspopup="dialog"
        aria-expanded={open}
        className="rounded-lg border border-line px-3 py-1.5 text-xs text-charcoal hover:bg-sand"
      >
        Views
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Saved views"
          className="absolute right-0 top-full z-20 mt-1 w-72 rounded-lg border border-line bg-cream shadow-lg"
        >
          {mode.kind === 'save' ? (
            <NameForm
              initial=""
              submitLabel="Save"
              error={formError}
              busy={busy}
              onSubmit={save}
              onCancel={() => {
                setMode({ kind: 'idle' });
                setFormError(null);
              }}
            />
          ) : (
            <div className="border-b border-line px-3 py-2">
              <button
                type="button"
                onClick={() => {
                  setMode({ kind: 'save' });
                  setFormError(null);
                }}
                className="text-xs text-ck-accent-text hover:underline"
              >
                Save current view
              </button>
            </div>
          )}

          {list.loading && <p className="px-3 py-2 text-xs text-muted">Loading saved views…</p>}

          {list.error !== null && (
            <div className="px-3 py-2 text-xs text-muted">
              <p className="mb-1">{list.error}</p>
              <button type="button" onClick={load} className="text-ck-accent-text hover:underline">
                Retry
              </button>
            </div>
          )}

          {!list.loading && list.error === null && list.views.length === 0 && (
            <p className="px-3 py-2 text-xs text-muted">
              No saved views yet. Filter the page, then save it.
            </p>
          )}

          <ul className="max-h-80 overflow-y-auto">
            {list.views.map(view => {
              const stale = isStaleView(view, storage);
              return (
                <li key={view.id} className="border-b border-line/60 last:border-b-0">
                  {mode.kind === 'rename' && mode.id === view.id ? (
                    <NameForm
                      initial={mode.name}
                      submitLabel="Rename"
                      error={formError}
                      busy={busy}
                      onSubmit={name => rename(view.id, name)}
                      onCancel={() => {
                        setMode({ kind: 'idle' });
                        setFormError(null);
                      }}
                    />
                  ) : (
                    <div className="px-3 py-2">
                      <button
                        type="button"
                        disabled={stale}
                        aria-disabled={stale}
                        onClick={() => apply(view)}
                        className={`block w-full text-left text-xs ${
                          stale ? 'cursor-default text-muted' : 'text-charcoal hover:underline'
                        }`}
                      >
                        {view.name}
                      </button>
                      <p className="text-[11px] text-muted">
                        by {view.created_by_name ?? '(removed)'}
                      </p>
                      {stale && (
                        <p className="text-[11px] text-muted">
                          Saved under an older version of this page.
                        </p>
                      )}
                      {view.can_edit && (
                        <div className="mt-1 flex flex-wrap items-center gap-1">
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => void overwrite(view)}
                            className={actionClass}
                          >
                            Update to current view
                          </button>
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => {
                              setMode({ kind: 'rename', id: view.id, name: view.name });
                              setFormError(null);
                            }}
                            className={actionClass}
                          >
                            Rename
                          </button>
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => void remove(view)}
                            className={actionClass}
                          >
                            Delete
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
