import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { listProjects, listTodos } from './api';
import { RecordChip } from './components/RecordChip';
import { useTodosChanged } from './hooks';
import { STALE_DAYS } from './constants';
import { todoPath } from './publicMode';
import { LoadFailed, LoadingRows, TodoShell } from './TodoShell';
import type { Todo, TodoProject } from './types';
import { useTodoMeta } from './useTodoMeta';
import { daysSince, formatAge, parseUTC } from './util';

interface ReviewData {
  stale: Todo[];
  stalledProjects: TodoProject[];
}

const PAGE_LIMIT = 500;

/**
 * Weekly review, read-only: the numbers, the stale corners, and the projects quietly
 * going nowhere.
 */
export function ReviewPage() {
  const { filters } = useTodoMeta();
  const [data, setData] = useState<ReviewData | null>(null);
  const [failed, setFailed] = useState(false);

  const load = () => {
    Promise.all([
      listTodos({ status: 'next_action', limit: PAGE_LIMIT }),
      listTodos({ status: 'waiting_for', limit: PAGE_LIMIT }),
      listTodos({ status: 'delegated', limit: PAGE_LIMIT }),
      listProjects('active'),
    ]).then(([next, waiting, delegated, activeProjects]) => {
      const stale = [...next, ...waiting, ...delegated]
        .filter(t => daysSince(t.updated_at) >= STALE_DAYS)
        .sort((a, b) => parseUTC(a.updated_at).getTime() - parseUTC(b.updated_at).getTime());
      // "Stalled" = no NEXT ACTION. A project full of someday/waiting items has
      // open_count > 0 but still no decided next physical step.
      const projectsWithNext = new Set(
        next.map(t => t.project_id).filter((id): id is number => id !== null),
      );
      setData({
        stale,
        // If the page window filled, absence proves nothing — suppress the list
        // rather than falsely flag projects whose next action sits beyond it.
        stalledProjects: next.length >= PAGE_LIMIT
          ? []
          : activeProjects.filter(p => !projectsWithNext.has(p.id)),
      });
      setFailed(false);
    }).catch(() => setFailed(true));
  };
  useEffect(load, []);
  // Review owns its own fetch, so the undo broadcast has to be taken explicitly (#231).
  // Without it, undoing a project's next action from here leaves that project listed
  // under "no next action" until the page is remounted.
  useTodosChanged(load);

  const counts = filters?.status_counts;
  const tiles: { label: string; value: number; to: string }[] = counts ? [
    { label: 'Inbox', value: counts.inbox, to: todoPath('/inbox') },
    { label: 'Next', value: counts.next_action, to: todoPath('/next') },
    { label: 'Waiting', value: counts.waiting_for + counts.delegated, to: todoPath('/waiting') },
    { label: 'Someday', value: counts.someday_maybe, to: todoPath('/someday') },
    { label: 'Done', value: counts.done, to: todoPath('/done') },
  ] : [];

  return (
    <TodoShell active="review" hideQuickAdd>
      <p className="text-sm text-muted">
        The weekly review: empty the inbox, give every active project a next action,
        follow up on stale waiting-fors, prune someday/maybe — and look at that Done count.
      </p>

      {counts && (
        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-5">
          {tiles.map(t => (
            <Link key={t.label} to={t.to}
                  className="rounded-xl border border-line-faint bg-cream p-3 text-center transition hover:border-brand/50">
              <p className="font-heading text-2xl font-bold text-charcoal">{t.value}</p>
              <p className="text-xs text-muted">{t.label}</p>
            </Link>
          ))}
        </div>
      )}

      {failed && <div className="mt-5"><LoadFailed retry={load} /></div>}
      {!failed && data === null && <LoadingRows />}
      {data && (
        <div className="mt-6 space-y-6">
          <section>
            <h2 className="mb-2 text-xs font-heading font-bold uppercase tracking-wide text-muted">
              Stale ({STALE_DAYS}+ days untouched)
            </h2>
            {data.stale.length === 0 ? (
              <p className="text-sm text-muted">Nothing stale. Clean system.</p>
            ) : (
              <div className="space-y-1">
                {data.stale.map(t => (
                  <div key={t.id}
                       className="flex items-start gap-2 rounded-lg border border-line-faint bg-cream px-3 py-2 text-sm">
                    {/* Wraps rather than truncates: deciding whether a stale item is
                        still worth doing needs the whole title. */}
                    <span className="min-w-0 flex-1 break-words text-charcoal">
                      {t.title}
                      {(t.deal_title || t.contact_name) && (
                        <span className="ml-1.5 text-xs"><RecordChip todo={t} /></span>
                      )}
                    </span>
                    <span className="shrink-0 text-xs text-muted">{formatAge(t.updated_at)}</span>
                  </div>
                ))}
              </div>
            )}
          </section>
          <section>
            <h2 className="mb-2 text-xs font-heading font-bold uppercase tracking-wide text-muted">
              Active projects without a next action
            </h2>
            {data.stalledProjects.length === 0 ? (
              <p className="text-sm text-muted">Every active project has a next action.</p>
            ) : (
              <div className="space-y-1">
                {data.stalledProjects.map(p => (
                  <Link key={p.id} to={todoPath(`/projects/${p.id}`)}
                        className="block rounded-lg border border-amber-300 bg-amber-50 dark:bg-amber-950/30 px-3 py-2 text-sm text-charcoal hover:border-brand/50">
                    {p.name}
                  </Link>
                ))}
              </div>
            )}
          </section>
        </div>
      )}
    </TodoShell>
  );
}
