import { describe, expect, it } from 'vitest';
import {
  activeMentionQuery, applyMention, filterMentionCandidates, hasMentionToken,
  keptMentionIds, MAX_MENTIONS, mentionLabel, mentionSegments,
} from './chatterMentions';

const ada = { id: 1, name: 'Ada Lovelace', email: 'ada@example.test' };
const ann = { id: 2, name: 'Ann', email: 'ann@example.test' };
const annabelle = { id: 3, name: 'Annabelle', email: 'belle@example.test' };
const blank = { id: 4, name: '  ', email: 'blank@example.test' };

describe('mentionLabel', () => {
  it('falls back to the email when the name is blank, like the server', () => {
    expect(mentionLabel(ada)).toBe('Ada Lovelace');
    expect(mentionLabel(blank)).toBe('blank@example.test');
  });
});

describe('activeMentionQuery', () => {
  it('opens at the start of the text or after whitespace', () => {
    expect(activeMentionQuery('@ad', 3)).toEqual({ start: 0, query: 'ad' });
    expect(activeMentionQuery('hi @', 4)).toEqual({ start: 3, query: '' });
    expect(activeMentionQuery('hi\n@an', 6)).toEqual({ start: 3, query: 'an' });
  });
  it('never opens inside an email address or after a space in the query', () => {
    expect(activeMentionQuery('ada@example', 11)).toBeNull();
    expect(activeMentionQuery('@ada lo', 7)).toBeNull();
    expect(activeMentionQuery('no mention', 10)).toBeNull();
  });
  it('reads up to the caret, not the end of the text', () => {
    expect(activeMentionQuery('@adaXYZ', 4)).toEqual({ start: 0, query: 'ada' });
  });
});

describe('filterMentionCandidates', () => {
  const users = [annabelle, ada, ann, blank];
  it('ranks prefix matches first and matches word starts and emails', () => {
    expect(filterMentionCandidates(users, 'ann').map(u => u.id)).toEqual([2, 3]);
    expect(filterMentionCandidates(users, 'love').map(u => u.id)).toEqual([1]);
    expect(filterMentionCandidates(users, 'belle@').map(u => u.id)).toEqual([3]);
  });
  it('an empty query lists everyone, capped', () => {
    expect(filterMentionCandidates(users, '', 2)).toHaveLength(2);
  });
});

describe('applyMention', () => {
  it('replaces the query with the label and a trailing space, and moves the caret', () => {
    expect(applyMention('hi @ad there', 3, 6, 'Ada Lovelace')).toEqual({
      text: 'hi @Ada Lovelace  there', caret: 17,
    });
  });
});

describe('keptMentionIds', () => {
  it('keeps only picked people whose token survives in the text', () => {
    const picked = [{ id: 1, label: 'Ada Lovelace' }, { id: 2, label: 'Ann' }];
    expect(keptMentionIds('@Ada Lovelace and @Ann', picked)).toEqual([1, 2]);
    expect(keptMentionIds('@Ada Lovelace and Ann', picked)).toEqual([1]);
  });
  it('does not find @Ann inside @Annabelle', () => {
    expect(hasMentionToken('ping @Annabelle', 'Ann')).toBe(false);
    expect(hasMentionToken('ping @Ann.', 'Ann')).toBe(true);
    expect(hasMentionToken('mail x@Ann', 'Ann')).toBe(false);
  });
  it('dedupes and caps at the server limit', () => {
    const picked = Array.from({ length: MAX_MENTIONS + 3 }, (_, i) => ({ id: i + 1, label: `u${i + 1}` }));
    const text = picked.map(p => `@${p.label}`).join(' ');
    expect(keptMentionIds(text, [...picked, picked[0]])).toHaveLength(MAX_MENTIONS);
  });
  it('treats label characters as literal text, not a pattern', () => {
    expect(hasMentionToken('@a.b', 'a+b')).toBe(false);
    expect(hasMentionToken('@a+b ok', 'a+b')).toBe(true);
  });
});

describe('mentionSegments', () => {
  it('highlights each mentioned name and leaves the rest plain', () => {
    expect(mentionSegments('@Ann can you ping @Ada Lovelace?', ['Ada Lovelace', 'Ann'])).toEqual([
      { text: '@Ann', mention: true },
      { text: ' can you ping ', mention: false },
      { text: '@Ada Lovelace', mention: true },
      { text: '?', mention: false },
    ]);
  });
  it('prefers the longer name when one is a prefix of another', () => {
    expect(mentionSegments('hi @Annabelle', ['Ann', 'Annabelle'])).toEqual([
      { text: 'hi ', mention: false }, { text: '@Annabelle', mention: true },
    ]);
  });
  it('a note with no mentions is one plain run', () => {
    expect(mentionSegments('plain @Ann', [])).toEqual([{ text: 'plain @Ann', mention: false }]);
  });
});
