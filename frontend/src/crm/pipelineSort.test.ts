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
    // `stage` is display-only by design — the list is stage-major already.
    for (const key of keys.filter(k => k !== 'stage')) {
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
      'title', 'company', 'stage', 'value', 'probability', 'score', 'touches',
      'closeDate', 'lastActivity', 'owner',
    ]);
  });
});
