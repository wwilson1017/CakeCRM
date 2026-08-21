/**
 * Shared keyword matching for browse filters.
 *
 * Pure and framework-free: no React, no I/O, no app imports. The tokenizer is lifted from the
 * search built in the blueprint, with two of its rules deliberately LEFT
 * BEHIND — see "What this does not do" below, which is the whole reason this file reads
 * shorter than the one it came from.
 *
 * ## The model
 *
 * Each row is normalized ONCE into a single space-padded document (`buildDoc`), and a query
 * is split into tokens (`tokenize`). A row matches when EVERY token appears somewhere in its
 * document — token-AND across the row, OR across the row's fields, substring within a token.
 * So "acme invoice" finds a row whose supplier is Acme and whose description mentions an
 * invoice, which is the Odoo-style behaviour the blueprint asks for and which no existing surface
 * had before: CRM and a sibling card-tracking surface each required the whole phrase to appear
 * contiguously inside ONE field.
 *
 * That makes this **strictly broader** than both matchers it replaces — every row that
 * matched before still matches. That property is not decorative. Changing matching on two
 * live surfaces at once is only safe in the direction where results can appear, never
 * disappear, because a disappearing result looks to the user like missing data.
 *
 * ## What this does not do, and why (the part that is easy to "restore" by mistake)
 *
 * An earlier review additionally anchors short tokens to whole words and drops stopwords. Both are
 * good rules **for a sibling surface's data**; neither is lifted here.
 *
 *   • **Anchoring** (a 1-char token, or a numeric token under 3 chars, must match a whole
 *     word) exists so `1` does not light up every serial number containing a 1. On the
 *     card-tracking surface's database it does the opposite, and it is the one rule that would break the
 *     strictly-broader property: a card with part number `ACM-1234` has the document
 *     `… acm 1234 …`, so typing `ACM-1` and `ACM-12` would stop matching while `ACM` and
 *     `ACM-123` still match — results vanish at keystroke 5, stay gone at 6 and reappear at
 *     7, on a box whose placeholder is "Search description, supplier, part #, barcode…".
 *     That is a search that looks broken while you use it.
 *   • **Stopword removal** is left out for a plainer reason: a stopword list is a hidden
 *     vocabulary that silently changes what a query means, and here it buys nothing. Under
 *     substring matching a short token already matches inside ordinary words, so dropping
 *     `a` from `warehouse a` and keeping it produce the SAME result set on this data — see
 *     the test that pins it. Fewer moving parts, and every token the user typed still has to
 *     appear somewhere.
 *
 * Both stay app-local to the sibling surface until a second consumer needs them; that is when
 * they earn a configuration knob, not before. Do not fold them in here as a "completeness"
 * fix — they are tuned to a data shape, and this module has no idea what data it is matching.
 *
 * ## The knobs those rules earned
 *
 * A sibling surface is now that second consumer: its CollectionView adoption filters through THIS
 * module, and adopting the shared defaults would be a real regression on the exact data the
 * rules were tuned for (`1` lighting up every card containing a 1; a sentence-shaped query
 * failing on stopwords). So the two rules moved here as OPT-INS, off by default:
 *
 *   • `tokenize(query, { stopwords })` — drop stopwords alongside other words; a stopword
 *     typed alone stays a real query, and an all-stopword multi-word query returns `[]`.
 *   • `docMatchesTokens(doc, tokens, { anchorShortTokens: true })` — an `isAnchored` token
 *     matches a whole word (` token `) instead of a substring.
 *
 * Defaults are byte-identical to before, so every other consumer (the card-tracking surface, CRM)
 * is untouched — the strictly-broader property above still holds for them. Only a caller that
 * passes the option gets that tuning behavior. `isAnchored` is exported so a delegate can
 * reproduce the rule and a test can pin it.
 *
 * ## The honest cost of substring tokens
 *
 * Because a token matches inside a longer word, `warehouse a` also matches "Warehouse B" —
 * the `a` is found inside `warehouse` itself. That is looser than a user typing a location
 * probably means. It is accepted rather than fixed here because the alternative (anchoring)
 * costs the part-number case above, which is worse, and because a surface that needs an exact
 * location has a location FACET for it — the facet is the precise instrument, the keyword box
 * is the broad one. Anything narrower than this is a per-app decision, made with knowledge of
 * that app's data.
 */

/**
 * Fold a value into the matchable alphabet: lowercase, every non-letter/non-digit run
 * collapsed to a space, trimmed, then padded with one space on each side.
 *
 * The padding is what makes whole-word matching expressible at all (`doc.includes(' 12 ')`),
 * and it is why `buildDoc` can join fields with a plain space without letting a token match
 * across a field boundary.
 */
export function normalize(value: string | null | undefined): string {
  if (!value) return '';
  // NFD + strip combining marks BEFORE the punctuation pass. Unicode has two spellings for an
  // accented letter, and macOS/iOS paste the decomposed one — under which `\p{M}` counts as
  // punctuation, so "Crème" would split into "cre me" and never match itself typed the other
  // way. Folding the accent away also makes `creme` find `Crème`, which is a broadening, in
  // keeping with this module's one-way safety property.
  const folded = String(value).normalize('NFD').replace(/\p{M}+/gu, '');
  return ` ${folded.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim()} `;
}

/**
 * Hard ceiling on tokens, so a pasted paragraph cannot make matching quadratic.
 * Carried over from the blueprint along with its drop-the-shortest rule.
 */
export const MAX_TOKENS = 12;

/**
 * Hard ceiling on the RAW query length, applied before anything else touches it.
 *
 * `MAX_TOKENS` bounds the token COUNT, which is not the same guarantee: a delimiter-free paste
 * normalizes to exactly one token as long as the input, so the cap never binds and
 * `normalize`'s full-string regex pass runs on the main thread on every settled keystroke. No
 * realistic query is anywhere near this long — it exists so a stray multi-megabyte paste on a
 * low-powered tablet is a no-op rather than a freeze.
 */
export const MAX_QUERY_CHARS = 512;

/** Opt-in tokenizer tuning. Omit for the shared defaults. */
export interface TokenizeOptions {
  /**
   * Words dropped from a MULTI-word query. A stopword typed ALONE is kept (a real query
   * someone means); an all-stopword multi-word query returns `[]` (announcing itself as
   * active while narrowing almost nothing is dishonest). Off when absent.
   */
  stopwords?: ReadonlySet<string>;
}

/**
 * Split a query into matchable tokens: normalized, deduplicated, capped.
 *
 * When the cap binds we drop the SHORTEST tokens rather than the last ones — the
 * discriminating words in a typed phrase tend to be the long ones (an asset name, a part
 * number), while the short ones are filler. Order is not preserved by that path and does not
 * need to be: matching is AND, so token order is irrelevant. Said explicitly so the next
 * reader does not assume otherwise.
 *
 * Pass `{ stopwords }` for the stopword rule; without it the behavior is
 * byte-identical to before.
 */
export function tokenize(query: string, opts?: TokenizeOptions): string[] {
  const raw = normalize(query.slice(0, MAX_QUERY_CHARS)).trim();
  if (!raw) return [];
  const seen = new Set<string>();
  const tokens: string[] = [];
  for (const word of raw.split(' ')) {
    if (!word || seen.has(word)) continue;
    seen.add(word);
    tokens.push(word);
  }
  // Stopword rule (opt-in): drop stopwords when they sit alongside other words, but keep a
  // lone stopword (a real query) and return [] for an all-stopword multi-word query.
  let kept = tokens;
  if (opts?.stopwords) {
    const meaningful = tokens.filter(t => !opts.stopwords!.has(t));
    kept = meaningful.length ? meaningful : (tokens.length === 1 ? tokens : []);
  }
  if (kept.length <= MAX_TOKENS) return kept;
  return [...kept].sort((a, b) => b.length - a.length).slice(0, MAX_TOKENS);
}

/**
 * ONE anchoring rule (moved here for its second consumer): a token matches a
 * whole WORD rather than a substring when it is a single character, or purely numeric and
 * shorter than 3. Without it `1` lights up every serial number containing a 1 and every
 * "Unit 12". Only consulted when `docMatchesTokens` is called with `{ anchorShortTokens: true }`.
 */
export function isAnchored(token: string): boolean {
  return token.length === 1 || (token.length < 3 && /^\p{N}+$/u.test(token));
}

/**
 * Build one searchable document from a row's fields.
 *
 * Call this once per row per data change, not once per keystroke: on a board of a few
 * thousand rows, re-normalizing ten fields per row per character is the dominant cost. Feed
 * it the raw field values; it normalizes each one.
 */
export function buildDoc(fields: (string | null | undefined)[]): string {
  return fields.map(normalize).join(' ');
}

/** Opt-in matcher tuning. Omit for the shared substring-only default. */
export interface MatchOptions {
  /** Match `isAnchored` tokens as whole words (` token `) instead of substrings. Off when absent. */
  anchorShortTokens?: boolean;
}

/**
 * Does a document built by `buildDoc` contain every token?
 *
 * An empty token list matches everything — a blank query imposes no constraint, which lets
 * callers skip a separate "is the search active" branch.
 *
 * Pass `{ anchorShortTokens: true }` for the whole-word rule on short tokens;
 * without it every token substring-matches, byte-identical to before.
 */
export function docMatchesTokens(doc: string, tokens: string[], opts?: MatchOptions): boolean {
  const anchor = opts?.anchorShortTokens === true;
  for (const token of tokens) {
    const hit = anchor && isAnchored(token) ? doc.includes(` ${token} `) : doc.includes(token);
    if (!hit) return false;
  }
  return true;
}
