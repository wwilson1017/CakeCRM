// Todo-GTD — quick-add natural-language parser.
//
// Hand-rolled fixed phrase set (repo has no date library, and due dates here
// are DATE-only — the easy 90% of parsing). Every recognized fragment is
// surfaced as a removable chip BEFORE submit, so a wrong parse is visible and
// harmless: dismissing the chip puts the text back in the title.
//
// Recognized: #project  @context  !  (star)
//   dates:    today, tomorrow, next week, in N days, <weekday>, on/next
//             <weekday>, aug 15 / august 15 [2027], 8/15, 2026-08-15
//   repeats:  every day|week|month|year, daily/weekly/monthly/yearly,
//             every weekday(s), every N days, every <weekday> (weekly,
//             anchored on the next such weekday)

export interface QuickAddChip {
  kind: 'project' | 'context' | 'star' | 'due' | 'repeat';
  label: string;
  /** The exact matched substring, so dismissing a chip can restore it. */
  raw: string;
}

export interface QuickAddParse {
  title: string;
  project?: string;
  context?: string;
  star: boolean;
  due_date?: string;
  repeat?: string;
  chips: QuickAddChip[];
}

const WEEKDAYS = [
  ['sunday', 'sun'], ['monday', 'mon'], ['tuesday', 'tue', 'tues'],
  ['wednesday', 'wed'], ['thursday', 'thu', 'thur', 'thurs'],
  ['friday', 'fri'], ['saturday', 'sat'],
];

const MONTHS = [
  ['january', 'jan'], ['february', 'feb'], ['march', 'mar'], ['april', 'apr'],
  ['may'], ['june', 'jun'], ['july', 'jul'], ['august', 'aug'],
  ['september', 'sep', 'sept'], ['october', 'oct'], ['november', 'nov'],
  ['december', 'dec'],
];

function weekdayIndex(word: string): number {
  return WEEKDAYS.findIndex(names => names.includes(word.toLowerCase()));
}

function monthIndex(word: string): number {
  return MONTHS.findIndex(names => names.includes(word.toLowerCase()));
}

function iso(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function addDays(base: Date, days: number): Date {
  const d = new Date(base.getFullYear(), base.getMonth(), base.getDate());
  d.setDate(d.getDate() + days);
  return d;
}

/** Next occurrence of `weekday` STRICTLY after today ("friday" on a Friday
 * means next week — you'd say "today" otherwise). */
function nextWeekday(now: Date, weekday: number): Date {
  const delta = ((weekday - now.getDay() + 7) % 7) || 7;
  return addDays(now, delta);
}

interface Span { start: number; end: number; chip: QuickAddChip }

const WEEKDAY_ALTS = WEEKDAYS.flat().join('|');
const MONTH_ALTS = MONTHS.flat().join('|');

export function parseQuickAdd(
  input: string,
  now: Date = new Date(),
  /** Raw fragments the user dismissed as chips — they stay plain title text. */
  ignore: string[] = [],
): QuickAddParse {
  const spans: Span[] = [];
  const out: QuickAddParse = { title: '', star: false, chips: [] };
  const used = (start: number, end: number) =>
    spans.some(s => start < s.end && end > s.start);

  const claim = (m: RegExpMatchArray, chip: QuickAddChip) => {
    if (ignore.includes(m[0].trim())) return false;
    const start = m.index ?? 0;
    if (used(start, start + m[0].length)) return false;
    spans.push({ start, end: start + m[0].length, chip });
    return true;
  };

  // Repeats first — "every monday" must win over the bare-weekday date rule.
  for (const m of input.matchAll(
    new RegExp(String.raw`(?<=^|\s)every\s+(${WEEKDAY_ALTS})(?=\s|$)`, 'gi'))) {
    const wd = weekdayIndex(m[1]);
    if (wd >= 0 && claim(m, { kind: 'repeat', label: `every ${WEEKDAYS[wd][0]}`, raw: m[0] })) {
      out.repeat = 'weekly';
      if (!out.due_date) out.due_date = iso(nextWeekday(now, wd));
    }
  }
  for (const m of input.matchAll(/(?<=^|\s)every\s+(\d{1,4})\s+days?(?=\s|$)/gi)) {
    if (claim(m, { kind: 'repeat', label: `every ${m[1]} days`, raw: m[0] })) {
      out.repeat = `every:${parseInt(m[1], 10)}`;
    }
  }
  const SIMPLE_REPEATS: [RegExp, string, string][] = [
    [/(?<=^|\s)(every\s+day|daily)(?=\s|$)/gi, 'daily', 'daily'],
    [/(?<=^|\s)(every\s+weekdays?)(?=\s|$)/gi, 'weekdays', 'weekdays'],
    [/(?<=^|\s)(every\s+week|weekly)(?=\s|$)/gi, 'weekly', 'weekly'],
    [/(?<=^|\s)(every\s+month|monthly)(?=\s|$)/gi, 'monthly', 'monthly'],
    [/(?<=^|\s)(every\s+year|yearly)(?=\s|$)/gi, 'yearly', 'yearly'],
  ];
  for (const [re, value, label] of SIMPLE_REPEATS) {
    for (const m of input.matchAll(re)) {
      if (claim(m, { kind: 'repeat', label, raw: m[0] })) out.repeat = value;
    }
  }

  // Dates. First match wins; later date phrases stay in the title.
  const setDue = (m: RegExpMatchArray, date: Date, label?: string) => {
    if (out.due_date && !out.repeat) return false;
    if (out.due_date && spans.some(s => s.chip.kind === 'due')) return false;
    if (!claim(m, { kind: 'due', label: label ?? iso(date), raw: m[0] })) return false;
    out.due_date = iso(date);
    return true;
  };

  // JS Date rolls impossible dates over (Feb 30 → Mar 2), which would submit
  // a date the user never typed — only claim a chip when the parts round-trip.
  const realDate = (y: number, mo: number, day: number): Date | null => {
    const d = new Date(y, mo, day);
    return d.getFullYear() === y && d.getMonth() === mo && d.getDate() === day ? d : null;
  };

  for (const m of input.matchAll(/(?<=^|\s)(\d{4})-(\d{2})-(\d{2})(?=\s|$)/g)) {
    const d = realDate(+m[1], +m[2] - 1, +m[3]);
    if (d) setDue(m, d);
  }
  for (const m of input.matchAll(/(?<=^|\s)today(?=\s|$)/gi)) {
    setDue(m, addDays(now, 0), 'today');
  }
  for (const m of input.matchAll(/(?<=^|\s)tomorrow(?=\s|$)/gi)) {
    setDue(m, addDays(now, 1), 'tomorrow');
  }
  for (const m of input.matchAll(/(?<=^|\s)next\s+week(?=\s|$)/gi)) {
    setDue(m, addDays(now, 7), 'next week');
  }
  for (const m of input.matchAll(/(?<=^|\s)in\s+(\d{1,3})\s+days?(?=\s|$)/gi)) {
    setDue(m, addDays(now, parseInt(m[1], 10)), `in ${m[1]} days`);
  }
  for (const m of input.matchAll(
    new RegExp(String.raw`(?<=^|\s)(?:on\s+|next\s+)?(${WEEKDAY_ALTS})(?=\s|$)`, 'gi'))) {
    const wd = weekdayIndex(m[1]);
    if (wd >= 0) setDue(m, nextWeekday(now, wd), WEEKDAYS[wd][0]);
  }
  for (const m of input.matchAll(new RegExp(
    String.raw`(?<=^|\s)(${MONTH_ALTS})\s+(\d{1,2})(?:\s+(\d{4}))?(?=\s|$)`, 'gi'))) {
    const mo = monthIndex(m[1]);
    const day = parseInt(m[2], 10);
    if (mo < 0) continue;
    let year = m[3] ? parseInt(m[3], 10) : now.getFullYear();
    // "aug 15" after Aug 15 means next year, not a date in the past.
    if (!m[3] && realDate(year, mo, day) && realDate(year, mo, day)! < addDays(now, 0)) {
      year += 1;
    }
    const d = realDate(year, mo, day); // "apr 31" is not a chip
    if (d) setDue(m, d);
  }
  for (const m of input.matchAll(/(?<=^|\s)(\d{1,2})\/(\d{1,2})(?=\s|$)/g)) {
    const mo = parseInt(m[1], 10) - 1;
    const day = parseInt(m[2], 10);
    if (mo < 0 || mo > 11) continue;
    let year = now.getFullYear();
    if (realDate(year, mo, day) && realDate(year, mo, day)! < addDays(now, 0)) year += 1;
    const d = realDate(year, mo, day);
    if (d) setDue(m, d);
  }

  // #project / @context / ! star
  for (const m of input.matchAll(/(?<=^|\s)#([\w][\w-]*)(?=\s|$)/g)) {
    if (!out.project && claim(m, { kind: 'project', label: `#${m[1]}`, raw: m[0] })) {
      out.project = m[1];
    }
  }
  for (const m of input.matchAll(/(?<=^|\s)@([\w][\w-]*)(?=\s|$)/g)) {
    if (!out.context && claim(m, { kind: 'context', label: `@${m[1]}`, raw: m[0] })) {
      out.context = `@${m[1]}`;
    }
  }
  for (const m of input.matchAll(/(?<=^|\s)!(?=\s|$)/g)) {
    if (!out.star && claim(m, { kind: 'star', label: '★ today', raw: m[0] })) {
      out.star = true;
    }
  }

  // Title = input minus consumed spans, whitespace collapsed.
  spans.sort((a, b) => a.start - b.start);
  let title = '';
  let cursor = 0;
  for (const s of spans) {
    title += input.slice(cursor, s.start);
    cursor = s.end;
  }
  title += input.slice(cursor);
  out.title = title.replace(/\s+/g, ' ').trim();
  out.chips = spans.map(s => s.chip);
  return out;
}
