export const MAX_UTTERANCE_CHARS = 250;
const SENTENCE_END = /[.!?…]["'”’)\]]*(?=\s)/g;

/** Cut prose into pieces of at most `max` characters, preferring sentence ends, then spaces. */
export function splitProse(text: string, max = MAX_UTTERANCE_CHARS): string[] {
  const out: string[] = [];
  let rest = text;
  while (rest.length > max) {
    // One past the limit, so a sentence ending exactly at `max` (whitespace after it) still counts.
    const window = rest.slice(0, max + 1);
    let cut = -1;
    for (const m of window.matchAll(SENTENCE_END)) cut = m.index + m[0].length;
    if (cut <= 0) cut = Math.max(window.lastIndexOf(' '), window.lastIndexOf('\n'));
    if (cut <= 0) cut = max;
    out.push(rest.slice(0, cut).trim());
    rest = rest.slice(cut).trim();
  }
  if (rest) out.push(rest);
  return out;
}
