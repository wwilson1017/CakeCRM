import { describe, it, expect } from 'vitest';
import { normalize, tokenize, buildDoc, docMatchesTokens, isAnchored, MAX_TOKENS, MAX_QUERY_CHARS } from './match';

const matches = (fields: (string | null | undefined)[], query: string) =>
  docMatchesTokens(buildDoc(fields), tokenize(query));

describe('normalize', () => {
  it('lowercases, collapses punctuation to spaces, and pads with single spaces', () => {
    expect(normalize('ACM-1234')).toBe(' acm 1234 ');
    expect(normalize('  Oven   #3  ')).toBe(' oven 3 ');
  });

  it('returns empty string for absent values rather than a bare pad', () => {
    expect(normalize(null)).toBe('');
    expect(normalize(undefined)).toBe('');
    expect(normalize('')).toBe('');
    // Punctuation-only input has nothing to match; it must not become a lone ' '.
    expect(normalize('---')).toBe('  ');
  });

  it('folds accents away, so both Unicode spellings of a word match each other', () => {
    // Unicode has two encodings for an accented letter. macOS/iOS paste the DECOMPOSED one,
    // in which the combining mark counts as punctuation — so "Crème" would have split into
    // "cre me" and never matched itself typed the precomposed way.
    const precomposed = 'Cr\u00e8me Br\u00fbl\u00e9e';
    const decomposed  = 'Cre\u0300me Bru\u0302le\u0301e';
    expect(normalize(precomposed)).toBe(' creme brulee ');
    expect(normalize(decomposed)).toBe(normalize(precomposed));
    // …and an unaccented query finds the accented row, which is a broadening.
    expect(matches([decomposed], 'creme')).toBe(true);
    expect(matches([precomposed], 'brulee')).toBe(true);
  });
});

describe('tokenize', () => {
  it('splits on the normalized separators and deduplicates', () => {
    expect(tokenize('ACM-1234 acm')).toEqual(['acm', '1234']);
  });

  it('returns no tokens for a blank or punctuation-only query, so nothing is constrained', () => {
    expect(tokenize('')).toEqual([]);
    expect(tokenize('   ')).toEqual([]);
    expect(tokenize('-- //')).toEqual([]);
  });

  it('keeps every typed token — no stopword list', () => {
    expect(tokenize('the mixer that keeps breaking')).toEqual(['the', 'mixer', 'that', 'keeps', 'breaking']);
  });

  it('pins the accepted looseness: a short token matches INSIDE a longer word', () => {
    // `warehouse a` also matches "Warehouse B", because `a` is found inside `warehouse`.
    // This is the documented cost of substring tokens. Dropping stopwords would give the
    // identical result here (tokens become just `warehouse`), which is exactly why no
    // stopword list is worth carrying — asserted both ways so the claim stays honest.
    expect(matches(['Warehouse A'], 'warehouse a')).toBe(true);
    expect(matches(['Warehouse B'], 'warehouse a')).toBe(true);
    expect(matches(['Warehouse B'], 'warehouse')).toBe(true);
    // A facet is the precise instrument for this; the keyword box is the broad one.
  });

  it('caps the RAW query length, which the token cap alone does not bound', () => {
    // A delimiter-free paste normalizes to exactly ONE token as long as the input, so
    // MAX_TOKENS never binds and normalize()'s regex pass would run over the whole thing.
    const huge = 'x'.repeat(MAX_QUERY_CHARS * 4);
    const tokens = tokenize(huge);
    expect(tokens).toHaveLength(1);
    expect(tokens[0]).toHaveLength(MAX_QUERY_CHARS);
  });

  it('caps the token count by dropping the SHORTEST tokens', () => {
    const query = 'aa bbb cccc ddddd eeeeee fffffff gggggggg hhhhhhhhh iiiiiiiiii jjjjjjjjjjj kkkkkkkkkkkk lllllllllllll m';
    const tokens = tokenize(query);
    expect(tokens).toHaveLength(MAX_TOKENS);
    // 13 tokens in, 12 out: the single shortest (`m`) is dropped, the longest all survive.
    expect(tokens).not.toContain('m');
    expect(tokens).toContain('aa');
    expect(tokens).toContain('lllllllllllll');
  });
});

describe('docMatchesTokens — the substring/AND model', () => {
  it('matches a token as a plain substring, including inside a longer run of characters', () => {
    // The regression this pins: an anchoring rule (whole-word for 1-char and short numeric
    // tokens) would make `ACM-1` and `ACM-12` stop matching a part number of `ACM-1234`,
    // so results would vanish at keystroke 5, stay gone at 6 and reappear at 7.
    const fields = ['Bearing assembly', 'ACM-1234', 'Warehouse A'];
    for (const q of ['ACM', 'ACM-1', 'ACM-12', 'ACM-123', 'ACM-1234', '1', '12', '1234']) {
      expect(matches(fields, q), `query ${q}`).toBe(true);
    }
  });

  it('ANDs across tokens but ORs across fields — the cross-field match neither predecessor had', () => {
    const fields = ['Bearing assembly', 'Acme Supply', 'Warehouse A'];
    expect(matches(fields, 'bearing acme')).toBe(true);
    expect(matches(fields, 'acme bearing')).toBe(true); // order-independent
    expect(matches(fields, 'bearing zzz')).toBe(false);
  });

  it('does not let a token match across a field boundary', () => {
    // Fields are individually padded and then joined, so "assemblyacme" cannot be formed.
    expect(matches(['Bearing assembly', 'Acme Supply'], 'assemblyacme')).toBe(false);
  });

  it('treats an empty token list as no constraint', () => {
    expect(docMatchesTokens(buildDoc(['anything']), [])).toBe(true);
    expect(docMatchesTokens('', [])).toBe(true);
  });

  it('ignores absent fields instead of matching them', () => {
    expect(matches([null, undefined, 'Acme'], 'acme')).toBe(true);
    expect(matches([null, undefined], 'acme')).toBe(false);
  });

  it('is strictly broader than a single-field substring match', () => {
    // Every query that matched the old per-field `field.includes(q)` still matches.
    const fields = ['Bearing assembly', 'Acme Supply'];
    expect(matches(fields, 'ring assem')).toBe(true);   // contiguous within one field
    expect(matches(fields, 'e supply')).toBe(true);
  });
});

describe('opt-in tuning knobs — defaults unchanged, options reproduce those tuning rules', () => {
  const STOPWORDS = new Set(['the', 'in', 'a', 'of', 'that']);

  it('tokenize without options keeps stopwords (byte-identical default)', () => {
    expect(tokenize('the mixer')).toEqual(['the', 'mixer']);
    expect(tokenize('in the')).toEqual(['in', 'the']);
  });

  it('tokenize with { stopwords } drops stopwords alongside other words', () => {
    expect(tokenize('the mixer that keeps breaking', { stopwords: STOPWORDS }))
      .toEqual(['mixer', 'keeps', 'breaking']);
  });

  it('tokenize with { stopwords } keeps a LONE stopword (a real query)', () => {
    expect(tokenize('the', { stopwords: STOPWORDS })).toEqual(['the']);
  });

  it('tokenize with { stopwords } returns [] for an all-stopword multi-word query', () => {
    expect(tokenize('in the', { stopwords: STOPWORDS })).toEqual([]);
  });

  it('isAnchored: single char and short numeric anchor; longer / non-numeric do not', () => {
    expect(isAnchored('1')).toBe(true);
    expect(isAnchored('a')).toBe(true);
    expect(isAnchored('12')).toBe(true);
    expect(isAnchored('123')).toBe(false);
    expect(isAnchored('ab')).toBe(false);
    expect(isAnchored('ove')).toBe(false);
  });

  it('docMatchesTokens without options substring-matches short tokens (default)', () => {
    const doc = buildDoc(['Serial A1B2', 'Oven 12']);
    expect(docMatchesTokens(doc, ['1'])).toBe(true); // substring inside A1B2 / 12
  });

  it('docMatchesTokens with { anchorShortTokens } matches a short token as a whole word only', () => {
    const doc = buildDoc(['Serial A1B2']); // → ' serial a1b2 ' — no standalone ' 1 '
    expect(docMatchesTokens(doc, ['1'], { anchorShortTokens: true })).toBe(false);
    const doc2 = buildDoc(['Oven 1']); // → ' oven 1 ' — standalone ' 1 '
    expect(docMatchesTokens(doc2, ['1'], { anchorShortTokens: true })).toBe(true);
  });

  it('docMatchesTokens with { anchorShortTokens } still substring-matches a normal token', () => {
    expect(docMatchesTokens(buildDoc(['Oven']), ['ove'], { anchorShortTokens: true })).toBe(true);
  });
})
