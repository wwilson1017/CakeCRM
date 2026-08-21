import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { QuickAdd } from './components/QuickAdd';
import { isTodoPublicMode, todoPath } from './publicMode';
import { useTodoMeta } from './useTodoMeta';

export type TodoTab =
  | 'today' | 'inbox' | 'next' | 'projects' | 'waiting'
  | 'someday' | 'done' | 'review' | 'search';

// `sub` is the path relative to the todo root ('' = Today); todoPath() resolves it
// against the active base (the CRM's /crm/tasks vs. the public router basename), so
// the same tabs render in both modes.
const TABS: { key: TodoTab; label: string; sub: string }[] = [
  { key: 'today', label: 'Today', sub: '' },
  { key: 'inbox', label: 'Inbox', sub: '/inbox' },
  { key: 'next', label: 'To Do', sub: '/next' },
  { key: 'projects', label: 'Projects', sub: '/projects' },
  { key: 'waiting', label: 'Waiting', sub: '/waiting' },
  { key: 'someday', label: 'Someday', sub: '/someday' },
  { key: 'done', label: 'Done', sub: '/done' },
  { key: 'review', label: 'Review', sub: '/review' },
  { key: 'search', label: 'Contexts', sub: '/search' },
];

interface Props {
  active: TodoTab;
  /** Hide QuickAdd on pages that would fight it with their own form (Review) or that
   * render their own copy lower down the page (Inbox). */
  hideQuickAdd?: boolean;
  onAdded?: () => void;
  children: React.ReactNode;
}

/**
 * Shared shell for every GTD page.
 *
 * Two mounts, one component: inside the CRM it renders into the existing content area
 * (CrmLayout already supplies the page chrome and nav), while the no-login public app
 * has no chrome around it and must paint its own. Hence the `isTodoPublicMode` branch
 * on the outer wrapper — everything inside it is identical in both modes.
 *
 * The app uses flat sibling routes, so this mounts fresh per page; meta comes from the
 * module-level stale-while-revalidate cache in useTodoMeta, which is what keeps the
 * inbox badge from flashing on tab switches.
 */
export function TodoShell({ active, hideQuickAdd, onAdded, children }: Props) {
  const { filters, refreshMeta } = useTodoMeta();
  const [query, setQuery] = useState('');
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const inboxCount = filters?.status_counts.inbox ?? 0;

  // Keep the header box in sync with ?q= while ON the results page — covers deep links
  // and Back/Forward; elsewhere the box stays a blank launcher. Adjust-during-render
  // (not an effect): re-sync only when the URL value changes, so typing is never
  // clobbered. `active` is the reliable signal in both modes — location.pathname
  // carries the router basename in public mode, so a literal path check would miss.
  const onSearchPage = active === 'search';
  const urlQuery = onSearchPage ? (searchParams.get('q') ?? '') : '';
  const [prevUrlQuery, setPrevUrlQuery] = useState(urlQuery);
  if (prevUrlQuery !== urlQuery) {
    setPrevUrlQuery(urlQuery);
    setQuery(urlQuery);
  }

  const body = (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-heading text-2xl font-bold text-charcoal">Tasks</h1>
          <p className="text-sm text-muted">Capture everything. Mind like water.</p>
        </div>
        <form
          onSubmit={e => {
            e.preventDefault();
            navigate(todoPath(`/search?q=${encodeURIComponent(query.trim())}`));
          }}
        >
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search…"
            aria-label="Search all todos"
            className="w-32 sm:w-44 rounded-lg border border-line bg-cream px-3 py-1.5 text-base sm:text-sm text-charcoal placeholder:text-muted focus:border-brand focus:outline-none"
          />
        </form>
      </div>

      <div className="mt-4 flex gap-1 overflow-x-auto border-b border-line-faint">
        {TABS.map(tab => {
          const isActive = tab.key === active;
          return (
            <Link
              key={tab.key}
              to={todoPath(tab.sub)}
              className={`whitespace-nowrap px-3 sm:px-4 py-2.5 text-sm transition-colors border-b-2 -mb-px ${
                isActive
                  ? 'text-charcoal font-semibold border-brand'
                  : 'text-muted font-medium border-transparent hover:text-charcoal'
              }`}
            >
              {tab.label}
              {tab.key === 'inbox' && inboxCount > 0 && (
                <span className="ml-1.5 rounded-full bg-brand px-1.5 py-0.5 text-xs font-bold text-white">
                  {inboxCount}
                </span>
              )}
            </Link>
          );
        })}
      </div>

      {!hideQuickAdd && (
        <div className="mt-4">
          <QuickAdd onAdded={() => { void refreshMeta(); onAdded?.(); }} />
        </div>
      )}

      <div className="mt-5">{children}</div>
    </>
  );

  if (isTodoPublicMode) {
    return (
      <div className="min-h-screen bg-sand">
        <div className="mx-auto max-w-3xl px-4 pb-10 pt-6">{body}</div>
      </div>
    );
  }
  return <div className="mx-auto max-w-3xl">{body}</div>;
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-line-faint bg-cream p-8 text-center">
      <p className="font-heading font-semibold text-charcoal">{title}</p>
      {hint && <p className="mt-1 text-sm text-muted">{hint}</p>}
    </div>
  );
}

/**
 * Shown when a client-side filter hides every row of a non-empty list — distinct from
 * the true empty states above, which mean the LIST is empty.
 */
export function FilterEmptyState() {
  return (
    <div className="rounded-xl border border-line-faint bg-cream p-8 text-center">
      <p className="text-sm text-muted">No todos match your filter.</p>
    </div>
  );
}

export function LoadingRows() {
  return <p className="py-6 text-center text-sm text-muted">Loading…</p>;
}

export function LoadFailed({ retry }: { retry: () => void }) {
  return (
    <div className="rounded-xl border border-red-300 bg-red-50 dark:bg-red-950/30 p-4 text-sm text-charcoal">
      Couldn't load todos.{' '}
      <button type="button" onClick={retry} className="underline">Retry</button>
    </div>
  );
}
