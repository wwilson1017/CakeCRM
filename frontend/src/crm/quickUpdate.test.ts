import { describe, expect, it } from 'vitest';
import { buildBody, closedSuffix, confirmBody, parseValueInput, valueLabel, type QuickUpdatePreview } from './quickUpdate';
import { money } from './money';
const preview: QuickUpdatePreview = { deal: { id: 5, title: 'Canopy', stage: 'qualified', value: 10, currency: 'USD' }, body: { value: 2500 }, changes: [], needs: ['note'], warnings: [] };
describe('quickUpdate', () => {
  it('buildBody includes only ON tiles with content', () => {
    expect(buildBody({ note: true, value: false, stage: true, followup: false }, { note: ' called ', value: '10', followup: 'Call Ada' }, 'proposal')).toEqual({ note: 'called', stage: 'proposal' });
    expect(buildBody({ note: true, value: true, stage: false, followup: true }, { note: ' ', value: '0', followup: ' Call Ada ' }, 'proposal')).toEqual({ value: 0, followup: 'Call Ada' });
  });
  it('buildBody omits an unparseable value', () => {
    expect(buildBody({ note: false, value: true, stage: false, followup: false }, { note: '', value: 'abc', followup: '' }, null)).toEqual({});
  });
  it('parseValueInput accepts zero and rejects blank, garbage, negative and infinite', () => {
    expect(parseValueInput('0')).toBe(0);
    expect(parseValueInput('$2,500')).toBe(2500);
    for (const value of ['', ' ', 'abc', '-5', '1e999']) expect(parseValueInput(value)).toBeNull();
  });
  it('confirmBody waits for the requested note then merges it trimmed', () => {
    expect(confirmBody(preview, ' ')).toBeNull();
    expect(confirmBody(preview, ' called ')).toEqual({ value: 2500, note: 'called' });
    expect(confirmBody({ ...preview, needs: [] }, 'ignored')).toEqual(preview.body);
  });
  it('valueLabel uses the shared money formatter and says when there is no value', () => {
    expect(valueLabel(2500, 'USD')).toBe(money(2500, 'USD'));
    expect(valueLabel(2500, 'EUR')).toBe(money(2500, 'EUR'));
    expect(valueLabel(null, 'USD')).toBe('no value');
  });
  it('labels closed stages', () => {
    expect(closedSuffix('won')).toBe(' · WON');
    expect(closedSuffix('lost')).toBe(' · LOST');
    expect(closedSuffix('proposal')).toBe('');
  });
});
