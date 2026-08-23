import { describe, expect, it } from 'vitest';
import { displayName, sortFiles, writerLabel } from './kindLabel';
import type { ContextFileMeta } from './api';

const file = (filename: string, kind: ContextFileMeta['kind']): ContextFileMeta => ({
  id: 1, filename, kind, headline: '', is_protected: false, written_by: 'assistant',
  size_chars: 0, created_at: '', updated_at: '',
});

describe('displayName', () => {
  it('strips the folder and the .md extension', () => {
    expect(displayName('topics/pricing.md')).toBe('pricing');
    expect(displayName('daily/2026-08-21.md')).toBe('2026-08-21');
    expect(displayName('soul.md')).toBe('soul');
  });

  it('leaves a name with no extension alone', () => {
    expect(displayName('README')).toBe('README');
  });
});

describe('writerLabel', () => {
  it('names the assistant explicitly so a surprise rewrite is visible', () => {
    expect(writerLabel('assistant')).toBe('Baker');
    expect(writerLabel('user')).toBe('You');
    expect(writerLabel('system')).toBe('Built-in');
  });
});

describe('sortFiles', () => {
  it('puts identity first, then the snapshot, then topics, then dailies', () => {
    const sorted = sortFiles([
      file('daily/2026-08-20.md', 'daily'),
      file('topics/pricing.md', 'topic'),
      file('MEMORY.md', 'memory'),
      file('soul.md', 'soul'),
    ]);
    expect(sorted.map((f) => f.filename)).toEqual([
      'soul.md', 'MEMORY.md', 'topics/pricing.md', 'daily/2026-08-20.md',
    ]);
  });

  it('sorts topics alphabetically but daily notes newest-first', () => {
    const sorted = sortFiles([
      file('topics/zebra.md', 'topic'),
      file('topics/alpha.md', 'topic'),
      file('daily/2026-08-01.md', 'daily'),
      file('daily/2026-08-21.md', 'daily'),
    ]);
    expect(sorted.map((f) => f.filename)).toEqual([
      'topics/alpha.md', 'topics/zebra.md', 'daily/2026-08-21.md', 'daily/2026-08-01.md',
    ]);
  });

  it('does not mutate its input', () => {
    const input = [file('topics/b.md', 'topic'), file('soul.md', 'soul')];
    const before = input.map((f) => f.filename);
    sortFiles(input);
    expect(input.map((f) => f.filename)).toEqual(before);
  });
});
