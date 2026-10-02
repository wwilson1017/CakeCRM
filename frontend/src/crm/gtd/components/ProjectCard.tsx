import { Link } from 'react-router-dom';
import { PROJECT_STATUS_META } from '../constants';
import { todoPath } from '../publicMode';
import type { TodoProject } from '../types';

interface Props {
  project: TodoProject;
  /** True only for an ACTIVE project with no next action — the GTD "stalled" warning. */
  stalled: boolean;
  onCloseOut: (p: TodoProject) => void;
}

/**
 * One cell of the Projects grid (#234, port of cake_os #1840's `ProjectCard`).
 *
 * Supplied through `CollectionCardsProps.renderCard` because it has two targets — open the
 * project, and complete/reactivate it — while the layer's built-in card cell is one `<button>`.
 * Module scope, never declared inside the page: a component defined in another is a new identity
 * every render, so React would remount the grid on each keystroke.
 *
 * **A stretched link, not a wrapping one.** Before #234 the card WAS the `<Link>` and the button
 * sat inside it, held back from navigating by `preventDefault`/`stopPropagation` — but an `<a>`
 * may not contain interactive content, and that nesting announces the button as part of the
 * link. Here the link is an absolutely-positioned overlay covering the card, the text sits above
 * it with `pointer-events-none` so clicks fall through to the link, and only the button opts back
 * in. One card, two honest targets, no nesting — so the button needs no event suppression.
 *
 * `todoPath` keeps the link working under the no-login `/todo` mount, where routes hang off '/'.
 */
export function ProjectCard({ project, stalled, onCloseOut }: Props) {
  const closed = project.status === 'completed' || project.status === 'dropped';
  const meta = PROJECT_STATUS_META[project.status];
  return (
    <div className="relative h-full rounded-xl border border-line-faint bg-cream p-4 transition hover:border-brand/50">
      <Link
        to={todoPath(`/projects/${project.id}`)}
        className="absolute inset-0 rounded-xl focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
      >
        <span className="sr-only">Open project {project.name}</span>
      </Link>
      {/* `flex-wrap`: the layer's card grid ramps up to four columns, and in a narrow cell the
          chip drops under the title instead of crushing it. */}
      <div className="pointer-events-none relative flex flex-wrap items-start justify-between gap-2">
        <p className="min-w-0 break-words font-heading font-semibold text-charcoal">{project.name}</p>
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${meta.chip}`}>{meta.label}</span>
      </div>
      {project.notes && (
        <p className="pointer-events-none relative mt-1 line-clamp-2 break-words text-sm text-muted">
          {project.notes}
        </p>
      )}
      <div className="pointer-events-none relative mt-3 flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-muted">
          {project.open_count} open item{project.open_count === 1 ? '' : 's'}
        </span>
        <button
          type="button"
          onClick={() => onCloseOut(project)}
          className="pointer-events-auto rounded-lg border border-line px-2.5 py-1 text-xs font-heading text-charcoal hover:bg-sand"
        >
          {closed ? 'Reactivate' : 'Complete'}
        </button>
      </div>
      {stalled && (
        <p className="pointer-events-none relative mt-2 rounded-lg bg-amber-50 px-2 py-1 text-xs text-amber-700 dark:bg-amber-950/30 dark:text-amber-300">
          No next action — decide the very next physical step.
        </p>
      )}
    </div>
  );
}
