/**
 * Column definitions and the one custom-facet control for the CRM list pages (issue #77).
 *
 * This is the rendering half of `collectionConfig.ts`: everything here returns JSX, so the
 * config module can stay JSX-free and testable in a node environment.
 *
 * Two conventions:
 *
 * - **A column `key` that matches a sort field's `value` becomes sortable**, because
 *   `CollectionListView` derives each column's `sortValue` from the sort field with the same
 *   key. So `key` is typed against the sort tables' literal union — a typo is a compile
 *   error rather than a header that silently refuses to sort.
 * - Inside `CollectionView` the styling idiom is the layer's Tailwind (resolved through
 *   `index.css`'s semantic aliases), not the pages' inline `crm/styles.ts` objects. Accent
 *   used as TEXT goes through `text-ck-accent-text`, never the raw accent — the brand red is
 *   only 3.15:1 on the dark card (#54).
 * - **Where the row's action is a ROUTE rather than an overlay, the title cell also carries a
 *   real `<Link>` with `stopPropagation`** (#148). Since that issue the row itself is a
 *   keyboard stop, but an anchor adds what no click handler can hand-roll: it announces as a
 *   link, and it carries Ctrl/Cmd-click, middle-click and right-click into a new tab —
 *   a middle click never fires `click` at all, so the row handler cannot see it. Contacts and
 *   Companies navigate and so take the anchor; Tasks opens a detail overlay and does not.
 *
 * The file exports no component, so `react-refresh/only-export-components` stays quiet.
 */
import { Link } from 'react-router-dom';
import { ChipButton } from '../shared/search';
import { IconCheck, IconX } from '../shared/icons';
import type { ListColumn } from '../shared/listview';
import type { CrmCompany, CrmContact, CrmTask } from '../core/types';
import { PriorityBadge, ScorePill, StatusBadge } from './components/badges';
import { LINE_STRONG, SAGE, tint } from '../shared/styles';
import { formatAge, dueLabel } from './gtd/util';
import {
  CONTACT_SORT_FIELDS, COMPANY_SORT_FIELDS, TASK_SORT_FIELDS,
  DONE_OPTIONS, type DoneFacetRenderers, type DonePreset,
} from './collectionConfig';

type ContactSortKey = (typeof CONTACT_SORT_FIELDS)[number]['value'];
type CompanySortKey = (typeof COMPANY_SORT_FIELDS)[number]['value'];
type TaskSortKey = (typeof TASK_SORT_FIELDS)[number]['value'];

/** A column that is deliberately display-only (no matching sort field, so not sortable). */
type Display<K extends string> = K;

const DASH = '—';

// ── Contacts ────────────────────────────────────────────────────────────────

export function buildContactColumns(): ListColumn<CrmContact>[] {
  const cols: (ListColumn<CrmContact> & { key: ContactSortKey | Display<'phone'> })[] = [
    {
      key: 'name',
      header: 'Name',
      // A REAL link, not only the row's handler — see the route-shaped-surface note in the
      // header. `stopPropagation` keeps a plain click from ALSO firing the row's navigate,
      // which is the same contract every interactive cell in this file follows.
      render: c => (
        <div className="min-w-0">
          <Link
            to={`/crm/contacts/${c.id}`}
            onClick={e => e.stopPropagation()}
            className="block truncate text-charcoal hover:text-ck-accent-text"
          >
            {c.name}
          </Link>
          {c.title && <div className="truncate text-xs text-muted">{c.title}</div>}
        </div>
      ),
    },
    {
      key: 'company',
      header: 'Company',
      className: 'hidden md:table-cell',
      // company_name (the FK join) is authoritative over the legacy free text (#35).
      render: c => <span className="text-muted">{c.company_name || c.company || DASH}</span>,
    },
    {
      key: 'email',
      header: 'Email',
      className: 'hidden sm:table-cell',
      render: c => <span className="text-muted">{c.email || DASH}</span>,
    },
    {
      key: 'phone',
      header: 'Phone',
      className: 'hidden lg:table-cell',
      render: c => <span className="text-muted">{c.phone || DASH}</span>,
    },
    {
      key: 'last_contact',
      header: 'Last contact',
      className: 'hidden lg:table-cell',
      // "Never" is a real, and the most urgent, state — not a blank.
      render: c => (
        <span className="text-muted" title={c.last_contact_at ?? 'No logged activity or notes'}>
          {c.last_contact_at ? formatAge(c.last_contact_at) : 'Never'}
        </span>
      ),
    },
    { key: 'status', header: 'Status', render: c => <StatusBadge status={c.status} /> },
    {
      key: 'lead_score',
      header: 'Score',
      align: 'right',
      render: c => (c.lead_score != null ? <ScorePill score={c.lead_score} compact /> : <span className="text-muted">{DASH}</span>),
    },
  ];
  return cols;
}

// ── Companies ───────────────────────────────────────────────────────────────

export function buildCompanyColumns(): ListColumn<CrmCompany>[] {
  const cols: (ListColumn<CrmCompany> & { key: CompanySortKey | Display<'phone'> })[] = [
    {
      key: 'name',
      header: 'Name',
      // A REAL link — see the route-shaped-surface note in the header.
      render: c => (
        <Link
          to={`/crm/companies/${c.id}`}
          onClick={e => e.stopPropagation()}
          className="text-charcoal hover:text-ck-accent-text"
        >
          {c.name}
        </Link>
      ),
    },
    {
      key: 'industry', header: 'Industry', className: 'hidden sm:table-cell',
      render: c => <span className="text-muted">{c.industry || DASH}</span>,
    },
    {
      key: 'domain', header: 'Domain', className: 'hidden md:table-cell',
      render: c => <span className="text-muted">{c.domain || DASH}</span>,
    },
    {
      key: 'phone', header: 'Phone', className: 'hidden lg:table-cell',
      render: c => <span className="text-muted">{c.phone || DASH}</span>,
    },
    { key: 'status', header: 'Status', render: c => <StatusBadge status={c.status} /> },
  ];
  return cols;
}

// ── Tasks ───────────────────────────────────────────────────────────────────

/**
 * `onToggleComplete` is invoked from a control INSIDE the row, so the click must not also
 * open the row's detail — hence `stopPropagation`, the same trick the blueprint's Company
 * cell uses for its cross-navigation button.
 */
export function buildTaskColumns(
  onToggleComplete: (task: CrmTask) => void, today: string,
): ListColumn<CrmTask>[] {
  const cols: (ListColumn<CrmTask> & { key: TaskSortKey | Display<'done'> })[] = [
    {
      key: 'done',
      header: <span className="sr-only">Done</span>,
      className: 'w-8',
      render: t => (
        <button
          type="button"
          aria-label={t.completed ? `Mark "${t.title}" incomplete` : `Mark "${t.title}" complete`}
          onClick={e => { e.stopPropagation(); onToggleComplete(t); }}
          style={{
            width: 20, height: 20, borderRadius: 4, flexShrink: 0,
            border: `1.5px solid ${t.completed ? SAGE : LINE_STRONG}`,
            background: t.completed ? tint(SAGE, 20) : 'transparent',
            cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: SAGE,
          }}
        >
          {!!t.completed && <IconCheck size={12} strokeWidth={2.5} />}
        </button>
      ),
    },
    {
      key: 'title',
      header: 'Task',
      render: t => {
        const linked = [t.contact_name, t.deal_title].filter(Boolean).join(' · ');
        return (
          <div className="min-w-0">
            <div className={`truncate ${t.completed ? 'text-muted line-through' : 'text-charcoal'}`}>{t.title}</div>
            {linked && <div className="hidden truncate text-xs text-muted sm:block">{linked}</div>}
          </div>
        );
      },
    },
    {
      key: 'priority', header: 'Priority', className: 'hidden sm:table-cell',
      render: t => <PriorityBadge priority={t.priority} />,
    },
    {
      key: 'due',
      header: 'Due',
      render: t => {
        if (!t.due_date) return <span className="text-muted">{DASH}</span>;
        // `today` is passed in from useLocalDay rather than read from the clock here: a
        // renderer only runs when React re-renders, so a tab left open past midnight would
        // otherwise keep comparing against the day it was opened.
        const { text, overdue } = dueLabel(t.due_date, today);
        // An overdue COMPLETED task is just a task that was finished late — no alarm.
        const late = overdue && !t.completed;
        // ck-red, not the brand accent: red-as-danger is the app-wide convention for
        // overdue (TaskDetailBody, the dashboard tiles, the badges), and the two tokens
        // are genuinely different colours in both themes.
        return <span className={late ? 'font-semibold text-ck-red' : 'text-muted'}>{text}</span>;
      },
    },
  ];
  return cols;
}

// ── The Done facet's control + chip ─────────────────────────────────────────

/**
 * Open / Done / All. Rendered into the bar's disclosure panel; the chip below is what shows
 * in the collapsed row while the facet is away from its "All" resting state.
 */
export function buildDoneFacetRenderers(): DoneFacetRenderers {
  return {
    renderControl: (value, setValue) => (
      <div>
        <div className="mb-1.5 text-xs font-medium text-charcoal">Show</div>
        <div className="flex flex-wrap gap-1.5">
          {DONE_OPTIONS.map(o => (
            <ChipButton
              key={o.value}
              label={o.label}
              active={value === o.value}
              onClick={() => setValue(o.value as DonePreset)}
            />
          ))}
        </div>
      </div>
    ),
    // Clearing this facet returns it to its DEFAULT ('open'), not to 'all' — the layer
    // passes `def.defaultValue` to the clear handler — so the chip says "reset", not "off".
    renderChip: (value, clear) => (
      <span className="inline-flex items-center gap-1 rounded-full bg-sand py-1 pl-2.5 pr-1 text-xs text-charcoal">
        {`Show: ${DONE_OPTIONS.find(o => o.value === value)?.label ?? value}`}
        <button
          type="button"
          onClick={clear}
          aria-label="Reset the Show filter to Open"
          className="inline-flex h-4 w-4 items-center justify-center rounded-full text-muted hover:bg-line/50 hover:text-charcoal"
        >
          <IconX className="h-3 w-3" aria-hidden="true" />
        </button>
      </span>
    ),
  };
}
