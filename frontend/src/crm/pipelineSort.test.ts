import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ReactElement } from 'react';
import type { CrmDeal } from '../core/types';
import type { SortFieldDef } from '../shared/search';
import { isManualSort, sortItems } from '../shared/search';
import { PIPELINE_DEFAULT_SORT, pipelineSortFields } from './pipelineSort';
import { buildPipelineListColumns } from './components/pipelineListColumns';

afterEach(() => vi.useRealTimers());

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
    ['closedOn', deal({ id: 1, closed_on: null })],
    ['lastActivity', deal({ id: 1, last_contact_at: null })],
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
      'closeDate', 'closedOn', 'lastActivity', 'owner',
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

  it('renders an em dash for an absent calendar date rather than "Invalid Date"', () => {
    expect(cell('closeDate', deal({ id: 1, expected_close_date: '' }))).toBe('—');
  });
});

// The pinned Chicago TZ makes a bare Date constructor read the zone-less input five
// hours late (2d instead of 3d), proving the column goes through formatAge → parseUTC.
describe('Last contact column', () => {
  const MICROS = '2026-05-17T12:00:00.123456+00:00';
  const column = () => buildPipelineListColumns(() => 'x').find(c => c.key === 'lastActivity')!;
  const rendered = (at: string | null) => (
    column().render(deal({ id: 1, last_contact_at: at })) as ReactElement<{ children: string; title: string }>
  ).props;

  it('uses the same Last contact label for the sort and column', () => {
    expect(column().header).toBe('Last contact');
    expect(fields.find(f => f.value === 'lastActivity')?.label).toBe('Last contact');
  });

  it('renders Never when no customer contact is recorded', () => {
    expect(rendered(null)).toMatchObject({ children: 'Never', title: 'No customer contact logged' });
  });

  it.each(['2026-05-17T12:00:00Z', '2026-05-17T12:00:00'])(
    'renders contact age through parseUTC: %s', at => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date('2026-05-20T12:00:00Z'));
      expect(rendered(at)).toMatchObject({ children: '3d', title: at });
    },
  );

  it('renders the six-fraction-digit shape Postgres returns', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-05-20T12:00:00Z'));
    expect(rendered(MICROS).children).not.toBe('Never');
  });

  it('sorts on the instant, so it is not treated as an absent value', () => {
    const field = pipelineSortFields(() => 'x').find(f => f.value === 'lastActivity')!;
    const older = field.get!(deal({ id: 1, last_contact_at: '2026-05-15T02:00:00.000001+00:00' }));
    const newer = field.get!(deal({ id: 2, last_contact_at: MICROS }));
    expect(older).not.toBeNull();
    expect(newer).not.toBeNull();
    expect(Number(newer)).toBeGreaterThan(Number(older));
  });

  it('orders the same instant identically however its zone is punctuated', () => {
    // `Z` sorts after `+` lexically, so the old string comparison called these two unequal.
    const field = pipelineSortFields(() => 'x').find(f => f.value === 'lastActivity')!;
    expect(field.get!(deal({ id: 1, last_contact_at: '2026-05-15T02:30:00.123Z' })))
      .toBe(field.get!(deal({ id: 2, last_contact_at: '2026-05-15T02:30:00.123+00:00' })));
  });

  it('sinks an unparseable timestamp rather than poisoning the comparison with NaN', () => {
    const field = pipelineSortFields(() => 'x').find(f => f.value === 'lastActivity')!;
    expect(field.get!(deal({ id: 1, last_contact_at: 'not a date' }))).toBeNull();
  });
});
