// Todo GTD — plain-text renderings of a todo, for the edit sheet's two Copy
// buttons.
//
// Mirrored in the blueprints (cake_os `apps/todo-gtd/copyText.ts`, chatty
// `todo/copyText.ts`) so a todo copied out of any of the three pastes in the
// same shape. Keep them in sync; the shape is pinned by copyText.test.ts on
// every side. The one deliberate difference is the status WORD, which comes
// from each app's own `STATUS_META` so the copy says what that app's chip says
// on screen.
//
// Pure on purpose: the sheet renders from live FORM state, not from the saved
// row, so what you copy is what is on screen — including edits you have not
// saved yet.

import { REPEAT_OPTIONS, STATUS_META } from './constants';
import type { TodoStatus } from './types';

export interface TodoCopyFields {
  title: string;
  notes: string;
  status: TodoStatus;
  /** Resolved project NAME, not its id — null when unfiled. */
  projectName: string | null;
  context: string;
  tags: string[];
  /** YYYY-MM-DD, or null when there is no due date. */
  dueDate: string | null;
  /** Repeat rule as stored: '' | daily | weekly | … | every:N */
  repeat: string;
  star: boolean;
}

/**
 * Just the next action — the single line you paste into a message, a calendar
 * entry, or another list. No label, no decoration: pasting should give you the
 * action and nothing to delete afterwards.
 */
export function nextActionCopyText(title: string): string {
  return title.trim();
}

/**
 * The repeat rule as the sheet words it, not as the column stores it.
 *
 * Same principle as the status word above: a pasted todo should say what the
 * screen says. `every:3` is storage syntax with no `REPEAT_OPTIONS` entry — the
 * sheet renders it as "Every N days…" plus a number — so printing it raw is the
 * form dump this module's header promises not to be. A deliberate divergence
 * from both blueprints, which print the raw rule.
 */
function repeatLabel(repeat: string): string {
  const known = REPEAT_OPTIONS.find(o => o.value === repeat);
  if (known) return known.label;
  if (repeat.startsWith('every:')) return `Every ${repeat.slice(6)} days`;
  return repeat;
}

/**
 * The whole todo as plain text: the action on line one, then only the fields
 * that are actually set, then the notes. Absent fields are omitted rather than
 * printed empty — a pasted todo should read like a note, not like a form dump.
 */
export function todoCopyText(f: TodoCopyFields): string {
  const blocks: string[] = [f.title.trim()];

  const meta: string[] = [`Status: ${STATUS_META[f.status].label}`];
  if (f.projectName) meta.push(`Project: ${f.projectName}`);
  if (f.context.trim()) meta.push(`Context: ${f.context.trim()}`);
  if (f.dueDate) meta.push(`Due: ${f.dueDate}`);
  if (f.repeat) meta.push(`Repeat: ${repeatLabel(f.repeat)}`);
  if (f.tags.length) meta.push(`Tags: ${f.tags.join(', ')}`);
  if (f.star) meta.push('Starred: yes');
  blocks.push(meta.join('\n'));

  const notes = f.notes.trim();
  if (notes) blocks.push(`Notes:\n${notes}`);

  return blocks.join('\n\n');
}
