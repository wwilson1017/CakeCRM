import { describe, expect, it } from 'vitest';
import type { CrmDeal } from '../core/types';
import type { SortFieldDef } from '../shared/search';
import { isManualSort, sortItems } from '../shared/search';
import { PIPELINE_DEFAULT_SORT, pipelineSortFields } from './pipelineSort';
import { buildPipelineListColumns } from './components/pipelineListColumns';

function deal(over: Partial<CrmDeal> & { id: number }): CrmDeal {
  return {
    contact_id: null, company_id: null, title: `Deal ${over.id}`, stage: 'lead',
    value: 0, notes: '', expected_close_date: '', probability: 0, currency: 'USD',
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    ...over,
  };
}

const fields = pipelineSortFields(id => (id === 1 ? 'Ada Lovelace' : `User ${id}`));
const get = (value: string) => {
  const f = fields.find(x => x.value === value) as Extract<SortFieldDef<CrmDeal>, { get: unknown }>;
  if (!f?.get) throw new Error(`sort field ${value} has no getter`);
  return f.get;
};

describe('absent values sort as null, never 0 or empty string', () => {
  // sort.ts sinks nulls to the bottom in BOTH directions; a 0 would climb to the top
  // ascending and put valueless deals above real ones.
  const cases: [string, CrmDeal][] = [
    ['title', deal({ id: 1, title: '' })],
    ['company', deal({ id: 1, company_name: undefined, contact_name: undefined })],
    ['value', deal({ id: 1, value: undefined as unknown as number })],
    ['probability', deal({ id: 1, probability: undefined as unknown as number })],
    ['score', deal({ id: 1, lead_score: null })],
    ['touches', deal({ id: 1, ai_touch_count: null })],
    ['closeDate', deal({ id: 1, expected_close_date: '' })],
    ['lastActivity', deal({ id: 1, last_activity_at: null })],
    ['owner', deal({ id: 1, owner_id: null })],
  ];

  for (const [field, d] of cases) {
    it(`${field} returns null when absent`, () => {
      expect(get(field)(d)).toBeNull();
    });
  }

  it('a real zero is still a number, not null — 0% probability sorts as 0', () => {
    expect(get('probability')(deal({ id: 1, probability: 0 }))).toBe(0);
    expect(get('value')(deal({ id: 1, value: 0 }))).toBe(0);
    expect(get('score')(deal({ id: 1, lead_score: 0 }))).toBe(0);
  });
});

describe('field getters', () => {
  it('company falls back to the contact name', () => {
    expect(get('company')(deal({ id: 1, contact_name: 'Ada' }))).toBe('ada');
    expect(get('company')(deal({ id: 2, company_name: 'Acme', contact_name: 'Ada' }))).toBe('acme');
  });

  it('owner sorts on the RESOLVED name, not the numeric id', () => {
    expect(get('owner')(deal({ id: 1, owner_id: 1 }))).toBe('ada lovelace');
  });

  it('text getters lowercase so sorting is case-insensitive', () => {
    expect(get('title')(deal({ id: 1, title: 'Zebra' }))).toBe('zebra');
  });
});

describe('the resting sort keeps drag legal and the list natural', () => {
  it('boardOrder is an arrayOrder field and the default sort is manual', () => {
    expect(isManualSort(PIPELINE_DEFAULT_SORT, fields)).toBe(true);
  });

  it('sorting by boardOrder preserves the array order it was handed', () => {
    const items = [deal({ id: 3 }), deal({ id: 1 }), deal({ id: 2 })];
    expect(sortItems([...items], fields, PIPELINE_DEFAULT_SORT).map(d => d.id)).toEqual([3, 1, 2]);
  });

  it('any other field is NOT manual order', () => {
    expect(isManualSort({ field: 'value', dir: 'asc' }, fields)).toBe(false);
  });
});

describe('list columns and sort fields cannot drift', () => {
  it('every sortable column key names a real sort field', () => {
    const fieldValues = new Set(fields.map(f => f.value));
    const keys = buildPipelineListColumns(() => 'x').map(c => c.key);
    // Two columns are display-only by design and so name no sort field: `stage` (the list is
    // stage-major already) and `temperature` (#125 — a lexical sort over
    // 'cold' | 'hot' | 'warm' orders the tiers wrongly while looking like it works, so
    // sorting by it needs a real field with an explicit rank, as its own change).
    for (const key of keys.filter(k => k !== 'stage' && k !== 'temperature')) {
      expect(fieldValues.has(key)).toBe(true);
    }
  });

  it('no column supplies its own sortValue, so header and dropdown cannot disagree', () => {
    for (const col of buildPipelineListColumns(() => 'x')) {
      expect(col.sortValue).toBeUndefined();
    }
  });

  it('the columns cover the fields a rep sorts by', () => {
    const keys = buildPipelineListColumns(() => 'x').map(c => c.key);
    expect(keys).toEqual([
      'title', 'temperature', 'company', 'stage', 'value', 'probability', 'score', 'touches',
      'closeDate', 'lastActivity', 'owner',
    ]);
  });
});

describe('date columns are parsed by KIND, not uniformly', () => {
  // Regression: `new Date('2026-05-15')` is UTC midnight, which renders as May 14 anywhere
  // west of UTC. The suite runs under TZ=America/Chicago precisely so this can fail.
  const cell = (key: string, d: CrmDeal) => {
    const col = buildPipelineListColumns(() => 'x').find(c => c.key === key)!;
    return col.render(d) as string;
  };

  it('a date-only expected_close_date renders its own calendar day', () => {
    expect(cell('closeDate', deal({ id: 1, expected_close_date: '2026-05-15' })))
      .toBe(new Date(2026, 4, 15).toLocaleDateString());
  });

  it('a full timestamp last_activity_at converts to the viewer local day', () => {
    // 03:00 UTC on the 16th is 22:00 on the 15th in Chicago — the conversion is correct here.
    expect(cell('lastActivity', deal({ id: 1, last_activity_at: '2026-05-16T03:00:00Z' })))
      .toBe(new Date(2026, 4, 15).toLocaleDateString());
  });

  it('renders an em dash for an absent date rather than "Invalid Date"', () => {
    expect(cell('closeDate', deal({ id: 1, expected_close_date: '' }))).toBe('—');
    expect(cell('lastActivity', deal({ id: 1, last_activity_at: null }))).toBe('—');
  });
});

// ── Postgres microseconds vs JavaScriptCore (found by Codex on #109's column) ────────────────
//
// `last_activity_at` is written from `datetime.now(timezone.utc).isoformat()`, so it carries SIX
// fractional digits. ECMA-262 defines three. V8 tolerates the extra ones and JavaScriptCore does
// not, so the bare `new Date(...)` was Invalid Date in Safari and every iOS browser — the column
// rendered "—" for every deal that had any activity, and the sort saw them all as absent. Both
// now go through `parseUTC`, which truncates to milliseconds.
//
// The suite runs under TZ=America/Chicago, so a UTC instant late in the day is the previous local
// calendar day — which is exactly what makes this assert the conversion rather than the string.
describe('a six-fraction-digit timestamp', () => {
  const MICROS = '2026-05-15T02:30:00.123456+00:00';

  it('renders the Last activity column through parseUTC, not the bare constructor', () => {
    // ASSERTED ON THE ZONE-LESS CASE ON PURPOSE. This runner is V8, which accepts six fractional
    // digits, so a microsecond string alone renders identically with or without the fix and the
    // obvious test would pass against the bug. What V8 and `parseUTC` DO disagree about is a
    // timestamp carrying no zone: the bare constructor reads it as LOCAL, `parseUTC` appends `Z`
    // and reads it as UTC. Pinning that is what proves this column goes through `parseUTC` at
    // all, which is the thing Safari needs. The microsecond truncation rides the same call.
    const col = buildPipelineListColumns(() => 'x').find(c => c.key === 'lastActivity')!;
    const rendered = col.render(deal({ id: 1, last_activity_at: '2026-05-15T02:30:00.123456' })) as string;
    // 02:30 UTC is the evening of the 14th in Chicago; read as local it would still be the 15th.
    expect(rendered).toBe(new Date(2026, 4, 14).toLocaleDateString());
  });

  it('still renders a zoned microsecond timestamp rather than an em dash', () => {
    // Cannot fail under V8 — it is here because it is the exact string Postgres returns and the
    // literal symptom Safari showed ("—" on every deal with activity). Kept as documentation of
    // the input, with the falsifiable half above.
    const col = buildPipelineListColumns(() => 'x').find(c => c.key === 'lastActivity')!;
    expect(col.render(deal({ id: 1, last_activity_at: MICROS })) as string).not.toBe('—');
  });

  it('sorts on the instant, so it is not treated as an absent value', () => {
    const field = pipelineSortFields(() => 'x').find(f => f.value === 'lastActivity')!;
    const older = field.get!(deal({ id: 1, last_activity_at: '2026-05-15T02:00:00.000001+00:00' }));
    const newer = field.get!(deal({ id: 2, last_activity_at: MICROS }));
    expect(older).not.toBeNull();
    expect(newer).not.toBeNull();
    expect(Number(newer)).toBeGreaterThan(Number(older));
  });

  it('orders the same instant identically however its zone is punctuated', () => {
    // `Z` sorts after `+` lexically, so the old string comparison called these two unequal.
    const field = pipelineSortFields(() => 'x').find(f => f.value === 'lastActivity')!;
    expect(field.get!(deal({ id: 1, last_activity_at: '2026-05-15T02:30:00.123Z' })))
      .toBe(field.get!(deal({ id: 2, last_activity_at: '2026-05-15T02:30:00.123+00:00' })));
  });

  it('sinks an unparseable timestamp rather than poisoning the comparison with NaN', () => {
    const field = pipelineSortFields(() => 'x').find(f => f.value === 'lastActivity')!;
    expect(field.get!(deal({ id: 1, last_activity_at: 'not a date' }))).toBeNull();
  });
});
