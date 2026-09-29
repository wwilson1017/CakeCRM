/**
 * Chatter @-mentions (issue #235) — the pure half, so it tests in Node.
 *
 * The composer's `@` picker resolves a mention to a user ID at the moment of picking and
 * inserts `@<label>` into the text. The request carries those IDs, and the server never
 * parses the message. The text only answers one question: is the token for a picked
 * person still there? Deleting `@Ada` from the draft un-mentions Ada, which is what
 * someone deleting it means.
 *
 * `mentionLabel` must produce the same string the server freezes into
 * `crm_chatter_mentions.display_name` (`COALESCE(NULLIF(btrim(name), ''), email)`), so
 * the thread can highlight exactly the token the composer inserted.
 *
 * Known limit: two picked people with the same label share one token, so deleting one of
 * two `@Alex` tokens keeps both mentioned. Both are named in the note either way.
 */

/** Mirrors `chatter_service.MAX_MENTIONS`. */
export const MAX_MENTIONS = 20;

/** How many people the picker lists at once. */
export const MENTION_MENU_LIMIT = 6;

export interface MentionUser {
  id: number;
  name: string;
  email: string;
}

export interface PickedMention {
  id: number;
  label: string;
}

export function mentionLabel(user: Pick<MentionUser, 'name' | 'email'>): string {
  return user.name.trim() || user.email;
}

/**
 * The `@query` the caret is inside, or null.
 *
 * An `@` opens a query only at the start of the text or after whitespace, so an email
 * address typed into a note (`ada@example.com`) never opens the menu. The query runs up
 * to the caret and cannot contain whitespace or another `@`.
 */
export function activeMentionQuery(
  text: string, caret: number,
): { start: number; query: string } | null {
  if (caret < 0 || caret > text.length) return null;
  for (let i = caret - 1; i >= 0; i--) {
    const ch = text[i];
    if (ch === '@') {
      if (i > 0 && !/\s/.test(text[i - 1])) return null;
      return { start: i, query: text.slice(i + 1, caret) };
    }
    if (/\s/.test(ch)) return null;
  }
  return null;
}

/** People matching the query by name or email, prefix matches first. */
export function filterMentionCandidates<T extends MentionUser>(
  users: T[], query: string, limit = MENTION_MENU_LIMIT,
): T[] {
  const q = query.trim().toLowerCase();
  const scored: { user: T; rank: number }[] = [];
  for (const user of users) {
    const name = mentionLabel(user).toLowerCase();
    const email = user.email.toLowerCase();
    let rank = -1;
    if (!q || name.startsWith(q) || email.startsWith(q)) rank = 0;
    else if (name.split(/\s+/).some(w => w.startsWith(q))) rank = 1;
    else if (name.includes(q) || email.includes(q)) rank = 2;
    if (rank >= 0) scored.push({ user, rank });
  }
  scored.sort((a, b) => a.rank - b.rank || mentionLabel(a.user).localeCompare(mentionLabel(b.user)));
  return scored.slice(0, limit).map(s => s.user);
}

/** Replace the `@query` between `start` and `caret` with `@<label> `. */
export function applyMention(
  text: string, start: number, caret: number, label: string,
): { text: string; caret: number } {
  const inserted = `@${label} `;
  return { text: text.slice(0, start) + inserted + text.slice(caret), caret: start + inserted.length };
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * `@<label>` at the start or after whitespace, and NOT followed by a letter, digit or `_`,
 * so `@Ann` is not found inside `@Annabelle`.
 */
function tokenPattern(labels: string[]): RegExp | null {
  const alts = [...new Set(labels.filter(Boolean))]
    .sort((a, b) => b.length - a.length)
    .map(escapeRegExp);
  if (alts.length === 0) return null;
  return new RegExp(`(^|\\s)@(${alts.join('|')})(?![\\p{L}\\p{N}_])`, 'gu');
}

export function hasMentionToken(text: string, label: string): boolean {
  const re = tokenPattern([label]);
  return re ? re.test(text) : false;
}

/** The IDs to send: picked people whose token is still in the text, deduped, capped. */
export function keptMentionIds(text: string, picked: PickedMention[]): number[] {
  const ids: number[] = [];
  for (const p of picked) {
    if (!ids.includes(p.id) && hasMentionToken(text, p.label)) ids.push(p.id);
  }
  return ids.slice(0, MAX_MENTIONS);
}

export interface MessageSegment {
  text: string;
  mention: boolean;
}

/** Split a posted note into plain runs and highlighted `@name` runs. */
export function mentionSegments(message: string, names: string[]): MessageSegment[] {
  const re = tokenPattern(names);
  if (!re) return [{ text: message, mention: false }];
  const out: MessageSegment[] = [];
  let last = 0;
  for (const m of message.matchAll(re)) {
    const at = (m.index ?? 0) + m[1].length;  // skip the leading whitespace group
    if (at > last) out.push({ text: message.slice(last, at), mention: false });
    const token = `@${m[2]}`;
    out.push({ text: token, mention: true });
    last = at + token.length;
  }
  if (last < message.length) out.push({ text: message.slice(last), mention: false });
  return out;
}
