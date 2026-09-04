import { describe, it, expect } from 'vitest';
import {
  EXPAND_ALL_MAX,
  appendTimelinePage,
  describeTimelineEntry,
  entryKey,
  groupTimelineByDate,
  isArchivedContact,
  isArchivedDeal,
  isOpenDeal,
  partitionArchived,
  rollupSummary,
  timelineSourceLabel,
} from './companyRollup';
import type { CrmContact, CrmDeal, CrmTimelineEntry } from '../core/types';

function deal(over: Partial<CrmDeal> = {}): CrmDeal {
  return {
    id: 1, contact_id: null, title: 'D', stage: 'lead', value: 100,
    expected_close_date: '', probability: 0, currency: 'USD', notes: '',
    created_at: '2026-01-01T00:00:00+00:00', updated_at: '2026-01-01T00:00:00+00:00',
    company_id: null, lost_reason: '', archived_at: null,
    ...over,
  } as CrmDeal;
}

function contact(over: Partial<CrmContact> = {}): CrmContact {
  return {
    id: 1, name: 'C', email: '', phone: '', company: '', title: '', source: '',
    status: 'active', tags: '', notes: '',
    created_at: '2026-01-01T00:00:00+00:00', updated_at: '2026-01-01T00:00:00+00:00',
    company_id: null,
    ...over,
  } as CrmContact;
}

function entry(over: Partial<CrmTimelineEntry> = {}): CrmTimelineEntry {
  return {
    source: 'note', id: 1, entity_type: 'deal', entity_id: 5, activity: null,
    message: 'hello', created_at: '2026-03-02T15:00:00+00:00', updated_at: null,
    archived: 0, actor_id: null, source_name: 'Q3 renewal', source_archived: false,
    ...over,
  };
}

describe('rollupSummary', () => {
  it('counts and sums only deals that are live AND in an open stage', () => {
    // The CakeCRM-specific trap: `stage` and `archived_at` are two independent axes, so an
    // archived deal sitting in an open stage must NOT be counted. A port that reads only the
    // blueprint's single `status` field would count it.
    const summary = rollupSummary(
      [
        deal({ id: 1, stage: 'proposal', value: 1000 }),
        deal({ id: 2, stage: 'won', value: 5000 }),
        deal({ id: 3, stage: 'lost', value: 7000 }),
        deal({ id: 4, stage: 'lead', value: 9000, archived_at: '2026-02-01T00:00:00+00:00' }),
      ],
      [],
    );
    expect(summary.openDealCount).toBe(1);
    expect(summary.openDealValue).toBe(1000);
  });

  it('treats a non-finite value as zero instead of poisoning the whole sum', () => {
    const summary = rollupSummary(
      [deal({ id: 1, value: NaN }), deal({ id: 2, value: 250 })],
      [],
    );
    expect(summary.openDealValue).toBe(250);
  });

  it('excludes archived contacts from the contact count', () => {
    const summary = rollupSummary([], [
      contact({ id: 1, status: 'active' }),
      contact({ id: 2, status: 'inactive' }),
      contact({ id: 3, status: 'archived' }),
    ]);
    expect(summary.contactCount).toBe(2);
  });

  it('reports zeros and unset partial flags for an empty account', () => {
    // Partial defaults to FALSE, never true — claiming a total is incomplete when it is not
    // would put a bogus "≥" in front of an exact number.
    expect(rollupSummary([], [])).toEqual({
      openDealCount: 0, openDealValue: 0, contactCount: 0,
      dealsPartial: false, contactsPartial: false,
    });
  });

  it('carries the truncation flags through independently', () => {
    const summary = rollupSummary([], [], { deals: true });
    expect(summary.dealsPartial).toBe(true);
    expect(summary.contactsPartial).toBe(false);
  });
});

describe('archived predicates', () => {
  it('reads deals on archived_at and contacts on status', () => {
    expect(isArchivedDeal(deal({ archived_at: '2026-02-01T00:00:00+00:00' }))).toBe(true);
    expect(isArchivedDeal(deal({ archived_at: null }))).toBe(false);
    expect(isArchivedContact(contact({ status: 'archived' }))).toBe(true);
    expect(isArchivedContact(contact({ status: 'inactive' }))).toBe(false);
  });

  it('treats an archived open-stage deal as not open', () => {
    expect(isOpenDeal(deal({ stage: 'lead', archived_at: '2026-02-01T00:00:00+00:00' }))).toBe(false);
    expect(isOpenDeal(deal({ stage: 'lead' }))).toBe(true);
    expect(isOpenDeal(deal({ stage: 'won' }))).toBe(false);
  });
});

describe('partitionArchived', () => {
  it('puts live rows first, keeps input order in each half, and does not mutate', () => {
    const rows = [
      deal({ id: 1 }),
      deal({ id: 2, archived_at: '2026-02-01T00:00:00+00:00' }),
      deal({ id: 3 }),
      deal({ id: 4, archived_at: '2026-02-02T00:00:00+00:00' }),
    ];
    const before = rows.map((r) => r.id);
    const { live, archived } = partitionArchived(rows, isArchivedDeal);
    expect(live.map((r) => r.id)).toEqual([1, 3]);
    expect(archived.map((r) => r.id)).toEqual([2, 4]);
    expect(rows.map((r) => r.id)).toEqual(before);
  });

  it('works on the contact axis too', () => {
    const { live, archived } = partitionArchived(
      [contact({ id: 1, status: 'archived' }), contact({ id: 2 })],
      isArchivedContact,
    );
    expect(live.map((c) => c.id)).toEqual([2]);
    expect(archived.map((c) => c.id)).toEqual([1]);
  });
});

describe('entryKey / appendTimelinePage', () => {
  it('keys on (source, id), so the same id from each table stays two rows', () => {
    // The whole reason the key exists: crm_chatter and activity_log have independent SERIAL
    // sequences, so note #2 and activity #2 are different rows. An id-only dedupe drops one.
    const held = [entry({ source: 'note', id: 2 })];
    const merged = appendTimelinePage(held, [
      entry({ source: 'note', id: 2 }),
      entry({ source: 'activity', id: 2, activity: 'call' }),
    ]);
    expect(merged.map(entryKey)).toEqual(['note:2', 'activity:2']);
  });

  it('preserves prev order and does not mutate it', () => {
    const prev = [entry({ id: 1 }), entry({ id: 2 })];
    const merged = appendTimelinePage(prev, [entry({ id: 3 })]);
    expect(merged.map((e) => e.id)).toEqual([1, 2, 3]);
    expect(prev).toHaveLength(2);
  });

  it('appends nothing when the incoming page is entirely already held', () => {
    const prev = [entry({ id: 1 })];
    expect(appendTimelinePage(prev, [entry({ id: 1 })])).toHaveLength(1);
  });
});

describe('groupTimelineByDate', () => {
  it('groups consecutive same-day entries into ordered pairs', () => {
    const groups = groupTimelineByDate([
      entry({ id: 1, created_at: '2026-03-02T15:00:00+00:00' }),
      entry({ id: 2, created_at: '2026-03-02T09:00:00+00:00' }),
      entry({ id: 3, created_at: '2026-03-01T09:00:00+00:00' }),
    ]);
    expect(groups).toHaveLength(2);
    expect(groups[0][1].map((e) => e.id)).toEqual([1, 2]);
    expect(groups[1][1].map((e) => e.id)).toEqual([3]);
  });

  it('never re-sorts: a day that recurs after a gap opens a second group', () => {
    // Grouping is CONSECUTIVE, which is only correct because the server orders the feed.
    // Re-grouping non-adjacent rows would silently reorder the page.
    const groups = groupTimelineByDate([
      entry({ id: 1, created_at: '2026-03-02T15:00:00+00:00' }),
      entry({ id: 2, created_at: '2026-03-01T15:00:00+00:00' }),
      entry({ id: 3, created_at: '2026-03-02T15:00:00+00:00' }),
    ]);
    expect(groups.map((g) => g[1].map((e) => e.id))).toEqual([[1], [2], [3]]);
  });

  it('draws the day boundary in LOCAL time, not UTC', () => {
    // vitest pins TZ=America/Chicago (see vitest.config.ts), so 2026-03-02T01:00Z is
    // 2026-03-01 19:00 locally and belongs to March 1 — the same reason pipelineFilters
    // derives `ymd` from local getters instead of toISOString(). A UTC-grouping
    // implementation would split these into two headers and show the reader a day that
    // never happened in their timezone.
    const groups = groupTimelineByDate([
      entry({ id: 1, created_at: '2026-03-02T01:00:00+00:00' }),
      entry({ id: 2, created_at: '2026-03-01T15:00:00+00:00' }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0][0]).toBe('March 1, 2026');
  });

  it('returns no groups for an empty feed', () => {
    expect(groupTimelineByDate([])).toEqual([]);
  });
});

describe('timelineSourceLabel / describeTimelineEntry', () => {
  it('labels all three source kinds and keeps an archived source named', () => {
    expect(timelineSourceLabel(entry({ entity_type: 'company', source_name: 'Acme' })))
      .toEqual({ kind: 'Company', name: 'Acme', archived: false });
    expect(timelineSourceLabel(entry({ entity_type: 'contact', source_name: 'Jane' })))
      .toEqual({ kind: 'Contact', name: 'Jane', archived: false });
    expect(timelineSourceLabel(entry({ entity_type: 'deal', source_archived: true })))
      .toEqual({ kind: 'Deal', name: 'Q3 renewal', archived: true });
  });

  it('renders a note as its message and an activity as kind · note', () => {
    expect(describeTimelineEntry(entry({ message: 'Left a voicemail' }))).toBe('Left a voicemail');
    expect(describeTimelineEntry(
      entry({ source: 'activity', activity: 'call', message: 'Left a voicemail' }),
    )).toBe('call · Left a voicemail');
  });

  it('renders a bare logged activity as its kind rather than an empty row', () => {
    expect(describeTimelineEntry(entry({ source: 'activity', activity: 'call', message: '' })))
      .toBe('call');
    expect(describeTimelineEntry(entry({ source: 'activity', activity: 'stage_change', message: '' })))
      .toBe('stage change');
  });
});

describe('EXPAND_ALL_MAX', () => {
  it('is the blueprint ceiling that keeps one click from storming the server', () => {
    expect(EXPAND_ALL_MAX).toBe(50);
  });
});
