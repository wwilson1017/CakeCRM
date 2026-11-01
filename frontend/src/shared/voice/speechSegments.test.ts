import { expect, it } from 'vitest';
import { splitProse } from './speechSegments';
it('splits at sentences within the limit', () => {
  const text = 'This is a sentence. '.repeat(50).trim();
  const pieces = splitProse(text);
  expect(pieces.every(p => p.length <= 250 && p.endsWith('.'))).toBe(true);
  expect(pieces.join(' ')).toBe(text);
});
it('counts a sentence ending exactly at the limit', () => {
  expect(splitProse('123456789. abc', 10)).toEqual(['123456789.', 'abc']);
});
it('falls back to word boundaries and hard cuts', () => {
  expect(splitProse('one two three', 8)).toEqual(['one two', 'three']);
  expect(splitProse('x'.repeat(600)).map(p => p.length)).toEqual([250, 250, 100]);
  expect(splitProse('')).toEqual([]);
});
