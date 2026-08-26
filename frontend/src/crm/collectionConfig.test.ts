import { describe, it, expect } from 'vitest';
import type { CrmCompany, CrmContact, CrmTask } from '../core/types';
import type { CrmUser } from './useUsers';
import {
  CONTACT_SORT_FIELDS, COMPANY_SORT_FIELDS, TASK_SORT_FIELDS,
  buildOwnerOptions, matchesScoreBand, matchesDuePreset, matchesDonePreset,
  coerceDonePreset, makeContactsCollectionConfig, makeCompaniesCollectionConfig,
  makeTasksCollectionConfig,
} from './collectionConfig';
import { buildContactColumns, buildCompanyColumns, buildTaskColumns, buildDoneFacetRenderers } from './listColumns';

const contact = (over: Partial<CrmContact> = {}): CrmContact => ({
  id: 1, name: 'Ada', email: '', phone: '', company: '', company_id: null, title: '',
  source: '', status: 'active', tags: '', notes: '', created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z', ...over,
});
const company = (over: Partial<CrmCompany> = {}): CrmCompany => ({
  id: 1, name: 'Acme', domain: '', industry: '', phone: '', address: '', notes: '',
  source: '', status: 'active', created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z', ...over,
});
const task = (over: Partial<CrmTask> = {}): CrmTask => ({
  id: 1, contact_id: null, deal_id: null, title: 'Call', description: '', due_date: '',
  completed: 0, priority: 'medium', created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z', ...over,
});
const user = (id: number, over: Partial<CrmUser> = {}): CrmUser => ({
  id, email: `u${id}@example.com`, name: `User ${id}`, role: 'member', is_active: true, ...over,
});

const NOW = new Date(2026, 4, 1, 12, 0);
const TODAY = '2026-05-01';

const getters = <T,>(fields: readonly { value: string; get?: (i: T) => unknown }[]) =>
  fields.filter(f => f.get).map(f => [f.value, f.get!] as const);

describe('sort getters', () => {
  // The layer sorts nulls to the BOTTOM in both directions; a 0 or '' is a real value that
  // would sort a blank straight to the top ascending. This is the invariant that keeps
  // "unset" out of the way rather than in front.
  it('return null (never 0 or empty string) for every blank contact field', () => {
    const blank = contact({ name: '', email: '', company: '', company_name: '', status: '', updated_at: '', lead_score: null, last_contact_at: null });
    for (const [name, get] of getters<CrmContact>(CONTACT_SORT_FIELDS)) {
      expect(get(blank), `contact sort field "${name}"`).toBeNull();
    }
  });

  it('return null for every blank company field', () => {
    const blank = company({ name: '', industry: '', domain: '', status: '', updated_at: '' });
    for (const [name, get] of getters<CrmCompany>(COMPANY_SORT_FIELDS)) {
      expect(get(blank), `company sort field "${name}"`).toBeNull();
    }
  });

  it('return null for every blank task field, including an unset due date', () => {
    const blank = task({ title: '', due_date: '', priority: '', created_at: '' });
    for (const [name, get] of getters<CrmTask>(TASK_SORT_FIELDS)) {
      // `open_due` is exempt and must be: it is a COMPOSITE whose first component
      // (completion) is never unknown, so it always has a real position. Its own
      // "undated sorts last" is handled inside the string, not by the null rule.
      if (name === 'open_due') continue;
      expect(get(blank), `task sort field "${name}"`).toBeNull();
    }
  });

  it('orders open-before-done, then by due date, in ONE composite key', () => {
    // The server's historical order. Without it the "All" view interleaves done and open
    // tasks, which the tab bar this page replaces never did.
    const get = TASK_SORT_FIELDS.find(f => f.value === 'open_due')!.get;
    const key = (over: Partial<CrmTask>) => get(task(over)) as string;
    const openEarly = key({ completed: 0, due_date: '2026-01-01' });
    const openLate = key({ completed: 0, due_date: '2026-12-01' });
    const openUndated = key({ completed: 0, due_date: '' });
    const doneEarly = key({ completed: 1, due_date: '2026-01-01' });

    expect(openEarly < openLate).toBe(true);          // due date orders within a group
    expect(openLate < openUndated).toBe(true);        // undated last WITHIN the open group
    expect(openUndated < doneEarly).toBe(true);       // …but still ahead of anything done
  });

  it('sorts lead score numerically, not as text', () => {
    const get = CONTACT_SORT_FIELDS.find(f => f.value === 'lead_score')!.get;
    expect(get(contact({ lead_score: 9 }))).toBe(9);
    // A score of 0 is a REAL score and must not collapse to null.
    expect(get(contact({ lead_score: 0 }))).toBe(0);
  });

  it('ranks task priority by urgency rather than alphabetically', () => {
    const get = TASK_SORT_FIELDS.find(f => f.value === 'priority')!.get;
    const rank = (p: string) => get(task({ priority: p })) as number;
    expect(rank('high')).toBeGreaterThan(rank('medium'));
    expect(rank('medium')).toBeGreaterThan(rank('low'));
    expect(get(task({ priority: 'nonsense' }))).toBeNull();
  });

  it('prefers the linked company name over the legacy free text (#35)', () => {
    const get = CONTACT_SORT_FIELDS.find(f => f.value === 'company')!.get;
    expect(get(contact({ company: 'stale text', company_name: 'Linked Co' }))).toBe('linked co');
    expect(get(contact({ company: 'Only Text', company_name: '' }))).toBe('only text');
  });

  it('maps an unparseable timestamp to null rather than epoch 0', () => {
    const get = CONTACT_SORT_FIELDS.find(f => f.value === 'last_contact')!.get;
    expect(get(contact({ last_contact_at: 'not-a-date' }))).toBeNull();
    expect(get(contact({ last_contact_at: null }))).toBeNull();
    expect(typeof get(contact({ last_contact_at: '2026-05-01T10:00:00Z' }))).toBe('number');
  });
});

describe('buildOwnerOptions', () => {
  it('declares no facet on a single-seat install', () => {
    expect(buildOwnerOptions([], 1)).toBeNull();
    expect(buildOwnerOptions([user(1)], 1)).toBeNull();
  });

  it('leads with Unassigned and marks me and departed colleagues', () => {
    const opts = buildOwnerOptions([user(1), user(2, { is_active: false })], 1)!;
    expect(opts[0]).toEqual({ value: 'unassigned', label: 'Unassigned' });
    expect(opts[1].label).toBe('User 1 (me)');
    expect(opts[2].label).toBe('User 2 (deactivated)');
  });

  it('falls back to the email when a user has no name', () => {
    const opts = buildOwnerOptions([user(1, { name: '   ' }), user(2)], null)!;
    expect(opts[1].label).toBe('u1@example.com');
  });
});

describe('owner facet', () => {
  const ownerFacetOf = (owners: ReturnType<typeof buildOwnerOptions>) =>
    makeContactsCollectionConfig({ columns: buildContactColumns(), owners, now: NOW })
      .facets!.find(f => f.key === 'owner');

  it('buckets a null OR absent owner_id as unassigned', () => {
    const facet = ownerFacetOf(buildOwnerOptions([user(1), user(2)], 1))!;
    const getValue = (facet as { getValue: (c: CrmContact) => unknown }).getValue;
    expect(getValue(contact({ owner_id: null }))).toBe('unassigned');
    expect(getValue(contact())).toBe('unassigned');       // pre-#60 payload: key absent
    expect(getValue(contact({ owner_id: 7 }))).toBe(7);
  });

  it('is absent entirely on a single-seat install', () => {
    expect(ownerFacetOf(null)).toBeUndefined();
  });
});

describe('matchesScoreBand', () => {
  it('cuts the bands where ScorePill colours them', () => {
    expect(matchesScoreBand(70, 'hot')).toBe(true);
    expect(matchesScoreBand(69, 'hot')).toBe(false);
    expect(matchesScoreBand(69, 'warm')).toBe(true);
    expect(matchesScoreBand(40, 'warm')).toBe(true);
    expect(matchesScoreBand(39, 'cool')).toBe(true);
    expect(matchesScoreBand(0, 'cool')).toBe(true);
  });

  it('treats never-scored as its own bucket, not as cool', () => {
    expect(matchesScoreBand(null, 'unscored')).toBe(true);
    expect(matchesScoreBand(undefined, 'unscored')).toBe(true);
    expect(matchesScoreBand(null, 'cool')).toBe(false);
    expect(matchesScoreBand(10, 'unscored')).toBe(false);
  });
});

describe('matchesDuePreset', () => {
  // The vitest config pins TZ=America/Chicago. 23:30 local on the 1st is already the 2nd in
  // UTC, which is exactly the drift the old toISOString()-based page had.
  const lateEvening = new Date(2026, 4, 1, 23, 30);

  it('uses the LOCAL calendar day, not the UTC one', () => {
    expect(matchesDuePreset(task({ due_date: '2026-05-01' }), 'today', lateEvening)).toBe(true);
    expect(matchesDuePreset(task({ due_date: '2026-05-02' }), 'today', lateEvening)).toBe(false);
  });

  it('counts only OPEN tasks as overdue', () => {
    const late = { due_date: '2026-04-01' };
    expect(matchesDuePreset(task({ ...late }), 'overdue', lateEvening)).toBe(true);
    // Finished late is still finished — it does not belong on an "Overdue" list.
    expect(matchesDuePreset(task({ ...late, completed: 1 }), 'overdue', lateEvening)).toBe(false);
  });

  it('includes both bounds of the next-7-days window and excludes the day after', () => {
    expect(matchesDuePreset(task({ due_date: '2026-05-01' }), 'next7', lateEvening)).toBe(true);
    expect(matchesDuePreset(task({ due_date: '2026-05-08' }), 'next7', lateEvening)).toBe(true);
    expect(matchesDuePreset(task({ due_date: '2026-05-09' }), 'next7', lateEvening)).toBe(false);
    // Already overdue is not "upcoming".
    expect(matchesDuePreset(task({ due_date: '2026-04-30' }), 'next7', lateEvening)).toBe(false);
  });

  it('matches an unset due date only under "none"', () => {
    expect(matchesDuePreset(task({ due_date: '' }), 'none', lateEvening)).toBe(true);
    for (const p of ['today', 'next7', 'overdue'] as const) {
      expect(matchesDuePreset(task({ due_date: '' }), p, lateEvening)).toBe(false);
    }
  });
});

describe('the Done facet', () => {
  it('defaults to Open, reproducing the old Pending-by-default page', () => {
    const config = makeTasksCollectionConfig({
      columns: buildTaskColumns(() => {}, TODAY), owners: null, doneFacet: buildDoneFacetRenderers(), now: NOW,
    });
    const done = config.facets!.find(f => f.key === 'done')!;
    expect(done.kind).toBe('custom');
    // Only a CUSTOM facet carries a defaultValue, which is the whole reason for the kind:
    // a single-select defaults to null (inactive) and could not hide done tasks at rest.
    expect((done as { defaultValue: unknown }).defaultValue).toBe('open');
  });

  it('reports itself ACTIVE while it is hiding rows, so the bar can say why', () => {
    const config = makeTasksCollectionConfig({
      columns: buildTaskColumns(() => {}, TODAY), owners: null, doneFacet: buildDoneFacetRenderers(), now: NOW,
    });
    const isActive = (config.facets!.find(f => f.key === 'done') as { isActive: (v: unknown) => boolean }).isActive;
    expect(isActive('open')).toBe(true);
    expect(isActive('done')).toBe(true);
    expect(isActive('all')).toBe(false);
  });

  it('selects the three states correctly', () => {
    expect(matchesDonePreset(task({ completed: 0 }), 'open')).toBe(true);
    expect(matchesDonePreset(task({ completed: 1 }), 'open')).toBe(false);
    expect(matchesDonePreset(task({ completed: 1 }), 'done')).toBe(true);
    expect(matchesDonePreset(task({ completed: 0 }), 'done')).toBe(false);
    expect(matchesDonePreset(task({ completed: 1 }), 'all')).toBe(true);
    expect(matchesDonePreset(task({ completed: 0 }), 'all')).toBe(true);
  });

  it('coerces junk from sessionStorage back to Open without throwing', () => {
    for (const junk of [undefined, null, 42, 'nonsense', {}, []]) {
      expect(coerceDonePreset(junk)).toBe('open');
    }
    expect(coerceDonePreset('done')).toBe('done');
    expect(coerceDonePreset('all')).toBe('all');
  });
});

describe('column keys line up with sort fields', () => {
  // CollectionListView derives a column's sortValue from the sort field with the SAME key,
  // so a key that matches nothing is a silently unsortable header. Display-only columns are
  // listed explicitly here, which is what makes the rest of the assertion meaningful.
  const check = (columns: { key: string }[], fields: readonly { value: string }[], displayOnly: string[]) => {
    const known = new Set<string>(fields.map(f => f.value));
    for (const c of columns) {
      if (displayOnly.includes(c.key)) {
        expect(known.has(c.key), `"${c.key}" is declared display-only but HAS a sort field`).toBe(false);
      } else {
        expect(known.has(c.key), `column "${c.key}" has no sort field and would not sort`).toBe(true);
      }
    }
  };

  it('for contacts, companies and tasks', () => {
    check(buildContactColumns(), CONTACT_SORT_FIELDS, ['phone']);
    check(buildCompanyColumns(), COMPANY_SORT_FIELDS, ['phone']);
    check(buildTaskColumns(() => {}, TODAY), TASK_SORT_FIELDS, ['done']);
  });
});

describe('config shape', () => {
  const configs = () => [
    makeContactsCollectionConfig({ columns: buildContactColumns(), owners: null, now: NOW }),
    makeCompaniesCollectionConfig({ columns: buildCompanyColumns(), owners: null }),
    makeTasksCollectionConfig({ columns: buildTaskColumns(() => {}, TODAY), owners: null, doneFacet: buildDoneFacetRenderers(), now: NOW }),
  ];

  it('declares the list view it defaults to (the hook throws otherwise)', () => {
    for (const c of configs()) {
      expect(c.defaultView).toBe('list');
      expect(c.list?.columns.length).toBeGreaterThan(0);
    }
  });

  it('persists the query, so a reload does not silently widen the list', () => {
    for (const c of configs()) expect(c.persistSearch).toBe(true);
  });

  it('gives each surface its own storage key', () => {
    const keys = configs().map(c => c.storage.key);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it('searches every field the retired server-side query matched', () => {
    const [contacts, companies] = configs();
    // search_contacts matched name/email/company/co.name/notes; search_companies name/domain/notes.
    const contactDoc = (contacts as unknown as { searchText: (c: CrmContact) => unknown[] })
      .searchText(contact({ name: 'n', email: 'e', company: 'c', company_name: 'cn', notes: 'note' }));
    expect(contactDoc).toEqual(expect.arrayContaining(['n', 'e', 'c', 'cn', 'note']));
    const companyDoc = (companies as unknown as { searchText: (c: CrmCompany) => unknown[] })
      .searchText(company({ name: 'n', domain: 'd', notes: 'note' }));
    expect(companyDoc).toEqual(expect.arrayContaining(['n', 'd', 'note']));
  });
});
