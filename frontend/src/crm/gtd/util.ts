// Todo-GTD — pure helpers (unit-tested in util.test.ts).

/**
 * Parse a Postgres TIMESTAMPTZ into a Date.
 *
 * Not merely cosmetic: `updated_at` carries SIX fraction digits, and ECMAScript
 * guarantees parsing for exactly three. V8 accepts the longer form; Safari need not,
 * and the build targets Safari. An Invalid Date would not just mis-render — `NaN >=
 * STALE_DAYS` is `false`, so on Safari a genuinely stale todo would be silently
 * dropped from the Review page instead of shown. Truncating to milliseconds keeps
 * every engine on the same answer.
 */
export function parseUTC(iso: string): Date {
  const trimmed = String(iso || '').replace(/(\.\d{3})\d+/, '$1');
  const hasZone = /[Zz]|[+-]\d\d:?\d\d$/.test(trimmed);
  return new Date(hasZone ? trimmed : trimmed + 'Z');
}

/**
 * Local YYYY-MM-DD. Due dates carry the user's LOCAL calendar intent, so overdue
 * comparisons must use the local date — `toISOString()` would flip items to overdue
 * during the evening west of Greenwich.
 */
export function todayStr(now: Date = new Date()): string {
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, '0');
  const d = String(now.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

export function parseTags(input: string): string[] {
  return input.split(',').map(t => t.trim()).filter(Boolean);
}

/**
 * Free-text filter behind the per-tab search boxes: a substring match across title,
 * notes, context, project name and tags. Context matching is deliberately NOT here —
 * it belongs to the multi-select facet in contextFacet.ts, and two ways to match a
 * context (one unreachable) is exactly the drift worth avoiding.
 */
export function matchesFilter(
  todo: {
    title: string; notes: string; context: string;
    project_name: string | null; tags: string[];
  },
  search: string,
): boolean {
  const q = search.trim().toLowerCase();
  if (!q) return true;
  return [todo.title, todo.notes, todo.context, todo.project_name || '', ...todo.tags]
    .some(field => field.toLowerCase().includes(q));
}

/** "3d" / "5w" age chip for Waiting items — how long something has sat. */
export function formatAge(isoTimestamp: string, now: Date = new Date()): string {
  const then = parseUTC(isoTimestamp);
  const days = Math.max(0, Math.floor((now.getTime() - then.getTime()) / 86_400_000));
  if (days < 1) return 'today';
  if (days < 7) return `${days}d`;
  if (days < 60) return `${Math.floor(days / 7)}w`;
  return `${Math.floor(days / 30)}mo`;
}

/** Days a todo has gone untouched (for the Review page's stale list). */
export function daysSince(isoTimestamp: string, now: Date = new Date()): number {
  return Math.floor((now.getTime() - parseUTC(isoTimestamp).getTime()) / 86_400_000);
}

/** Human label for a due date: Today / Tomorrow / Mon Aug 10, plus an overdue flag. */
export function dueLabel(due: string, today: string): { text: string; overdue: boolean } {
  if (!due) return { text: '', overdue: false };
  if (due === today) return { text: 'Today', overdue: false };
  const [y, m, d] = due.split('-').map(Number);
  // Build from parts: new Date('YYYY-MM-DD') parses as UTC midnight and renders the
  // previous day west of Greenwich.
  const date = new Date(y, m - 1, d);
  const [ty, tm, td] = today.split('-').map(Number);
  const t = new Date(ty, tm - 1, td);
  if (date.getTime() === t.getTime() + 86_400_000) return { text: 'Tomorrow', overdue: false };
  const text = date.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
  return { text, overdue: due < today };
}

/** Human label for a repeat value, including the `every:N` form. */
export function repeatLabel(repeat: string): string {
  if (!repeat) return '';
  const every = /^every:(\d+)$/.exec(repeat);
  if (every) {
    const n = Number(every[1]);
    return n === 1 ? 'Daily' : `Every ${n} days`;
  }
  return repeat.charAt(0).toUpperCase() + repeat.slice(1);
}
