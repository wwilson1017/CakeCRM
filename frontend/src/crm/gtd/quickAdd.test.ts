import { describe, expect, it } from 'vitest';
import { parseQuickAdd } from './quickAdd';

// Saturday 2026-08-01, noon local (vitest runs with TZ=America/Chicago).
const NOW = new Date(2026, 7, 1, 12, 0, 0);

describe('parseQuickAdd', () => {
  it('plain text is just a title', () => {
    const p = parseQuickAdd('order more boxes', NOW);
    expect(p).toMatchObject({ title: 'order more boxes', star: false, chips: [] });
    expect(p.due_date).toBeUndefined();
    expect(p.project).toBeUndefined();
  });

  it('parses the kitchen-sink example', () => {
    const p = parseQuickAdd('call Val tomorrow #Website @calls !', NOW);
    expect(p.title).toBe('call Val');
    expect(p.due_date).toBe('2026-08-02');
    expect(p.project).toBe('Website');
    expect(p.context).toBe('@calls');
    expect(p.star).toBe(true);
    expect(p.chips.map(c => c.kind).sort()).toEqual(
      ['context', 'due', 'project', 'star'],
    );
  });

  it('today and explicit ISO dates', () => {
    expect(parseQuickAdd('pay rent today', NOW).due_date).toBe('2026-08-01');
    expect(parseQuickAdd('pay rent 2026-09-15', NOW).due_date).toBe('2026-09-15');
  });

  it('bare weekday means the NEXT one, strictly after today', () => {
    // NOW is a Saturday; "saturday" must mean next week, not today.
    expect(parseQuickAdd('backup laptop saturday', NOW).due_date).toBe('2026-08-08');
    expect(parseQuickAdd('pay rent friday', NOW).due_date).toBe('2026-08-07');
    expect(parseQuickAdd('standup on mon', NOW).due_date).toBe('2026-08-03');
  });

  it('in N days and next week', () => {
    expect(parseQuickAdd('follow up in 3 days', NOW).due_date).toBe('2026-08-04');
    expect(parseQuickAdd('review next week', NOW).due_date).toBe('2026-08-08');
  });

  it('month-name dates roll to next year once past', () => {
    expect(parseQuickAdd('renew cert aug 15', NOW).due_date).toBe('2026-08-15');
    expect(parseQuickAdd('renew cert jul 15', NOW).due_date).toBe('2027-07-15');
    expect(parseQuickAdd('renew cert aug 15 2028', NOW).due_date).toBe('2028-08-15');
    expect(parseQuickAdd('renew cert 7/15', NOW).due_date).toBe('2027-07-15');
  });

  it('impossible dates never become chips (no JS Date rollover)', () => {
    // Date(2026, 1, 30) would silently roll to Mar 2 — the parser must
    // leave the text in the title instead of claiming a date the user
    // never typed.
    for (const input of ['pay rent 2026-02-30', 'pay rent apr 31', 'pay rent 2/30']) {
      const p = parseQuickAdd(input, NOW);
      expect(p.due_date).toBeUndefined();
      expect(p.chips).toHaveLength(0);
    }
  });

  it('simple repeats', () => {
    expect(parseQuickAdd('water plants daily', NOW).repeat).toBe('daily');
    expect(parseQuickAdd('submit payroll every week', NOW).repeat).toBe('weekly');
    expect(parseQuickAdd('deep clean every month', NOW).repeat).toBe('monthly');
    expect(parseQuickAdd('renew lease yearly', NOW).repeat).toBe('yearly');
    expect(parseQuickAdd('stretch every weekday', NOW).repeat).toBe('weekdays');
    expect(parseQuickAdd('change filter every 30 days', NOW).repeat).toBe('every:30');
  });

  it('"every monday" is weekly anchored on the next monday', () => {
    const p = parseQuickAdd('submit payroll every monday', NOW);
    expect(p.repeat).toBe('weekly');
    expect(p.due_date).toBe('2026-08-03');
    expect(p.title).toBe('submit payroll');
    // The weekday inside the repeat phrase must NOT also match as a date chip.
    expect(p.chips.filter(c => c.kind === 'due')).toHaveLength(0);
  });

  it('explicit date beats the repeat anchor', () => {
    const p = parseQuickAdd('submit payroll every monday starting 2026-09-07', NOW);
    expect(p.repeat).toBe('weekly');
    expect(p.due_date).toBe('2026-09-07');
  });

  it('only the first project/context/star tokens are consumed', () => {
    const p = parseQuickAdd('#a #b @x @y thing', NOW);
    expect(p.project).toBe('a');
    expect(p.context).toBe('@x');
    expect(p.title).toBe('#b @y thing');
  });

  it('mid-word tokens are not parsed', () => {
    const p = parseQuickAdd('email invoice#42 to sam@example.com', NOW);
    expect(p.project).toBeUndefined();
    expect(p.context).toBeUndefined();
    expect(p.title).toBe('email invoice#42 to sam@example.com');
  });

  it('dismissed chips leave their text in the title', () => {
    const p = parseQuickAdd('call Val tomorrow', NOW, ['tomorrow']);
    expect(p.due_date).toBeUndefined();
    expect(p.title).toBe('call Val tomorrow');
    expect(p.chips).toHaveLength(0);
  });

  it('a second date phrase stays in the title', () => {
    const p = parseQuickAdd('move meeting from friday to monday', NOW);
    expect(p.due_date).toBe('2026-08-07'); // first match wins
    expect(p.title).toContain('monday');
  });
});
