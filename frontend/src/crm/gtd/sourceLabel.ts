import type { Todo } from './types';

/**
 * Who added a todo, for every source that is NOT a person working in the app (#260).
 *
 * `todos.source` is stamped server-side by each write site with its own literal and is
 * never client-supplied (`crm.gtd_common.TODO_SOURCES`). A person's own todo (`ui`) and
 * any value this build does not recognise return null, so a row only ever claims a
 * provenance the server actually recorded.
 *
 * `capture_web` matters most: it is the one source an unauthenticated stranger can
 * produce through the public capture link (#204), so its label is what tells the user
 * the text was not written by anyone on the team.
 */
export interface TodoSourceLabel {
  /** The visible word. */
  text: string;
  /** The hover explanation. */
  title: string;
}

const LABELS: Record<string, TodoSourceLabel> = {
  agent: { text: 'Baker', title: 'Added by Baker, the assistant' },
  observer: {
    text: 'Observer',
    title: 'Added by Baker, which noticed it in a conversation',
  },
  capture_web: {
    text: 'Capture link',
    title: 'Added through the public capture link — anyone with the link can write here',
  },
  telegram: { text: 'Telegram', title: 'Captured from Telegram' },
};

export function todoSourceLabel(source: Todo['source']): TodoSourceLabel | null {
  return Object.hasOwn(LABELS, source) ? LABELS[source] : null;
}
