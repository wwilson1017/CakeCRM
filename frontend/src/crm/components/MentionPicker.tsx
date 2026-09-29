import { useCallback, useId, useLayoutEffect, useRef, useState } from 'react';
import type { KeyboardEvent, ReactNode, RefObject } from 'react';
import {
  activeMentionQuery, applyMention, filterMentionCandidates, keptMentionIds, mentionLabel,
} from '../chatterMentions';
import type { PickedMention } from '../chatterMentions';
import { useUsers } from '../useUsers';
import type { CrmUser } from '../useUsers';
import { BG_ELEV, FONT_SANS, HOVER, INK, INK_MUTE, LINE_STRONG, SHADOW } from '../../shared/styles';

export interface MentionPicker {
  /** Spread onto the textarea: caret tracking and the listbox ARIA wiring. */
  textareaProps: {
    onSelect: (e: React.SyntheticEvent<HTMLTextAreaElement>) => void;
    'aria-autocomplete': 'list';
    'aria-controls': string | undefined;
    'aria-activedescendant': string | undefined;
  };
  /** Call first in the textarea's onKeyDown; `true` means the menu consumed the key. */
  onKeyDown: (e: KeyboardEvent<HTMLTextAreaElement>) => boolean;
  /** Call from the textarea's onChange so the query follows the caret. */
  onChange: (el: HTMLTextAreaElement) => void;
  /** The listbox, or null. Render inside a `position: relative` wrapper around the textarea. */
  menu: ReactNode;
  /** The user IDs to send: people picked whose `@name` is still in the text. */
  mentionIds: () => number[];
  /** Forget every pick — after a successful post, so the next note starts clean. */
  reset: () => void;
}

/**
 * The `@` picker for a note textarea (issue #235).
 *
 * Typing `@` at the start of the text or after whitespace lists active seats from
 * `useUsers()`; ArrowUp/ArrowDown move, Enter or Tab picks, Escape closes the menu (and
 * only the menu: it `preventDefault`s, which `DetailModal`'s document Escape handler
 * defers to). Picking inserts `@<name> ` and records the user ID. The IDs are what the
 * request carries. The server never reads names out of the text.
 *
 * `initial` seeds the picked set when editing a note, from the note's own stored
 * mentions, so a person deactivated since the note was posted stays mentioned rather
 * than silently dropping off on an unrelated edit.
 */
export function useMentionPicker(
  text: string,
  setText: (next: string) => void,
  textareaRef: RefObject<HTMLTextAreaElement | null>,
  initial: PickedMention[] = [],
): MentionPicker {
  const { activeUsers } = useUsers();
  const [picked, setPicked] = useState<PickedMention[]>(initial);
  const [caret, setCaret] = useState<number | null>(null);
  // The start index of a query the user closed with Escape; it stays closed until the
  // caret leaves that `@`.
  const [dismissedAt, setDismissedAt] = useState<number | null>(null);
  const [highlight, setHighlight] = useState({ key: '', index: 0 });
  const pendingCaret = useRef<number | null>(null);
  const listId = useId();

  const query = caret === null ? null : activeMentionQuery(text, caret);
  const open = query && query.start !== dismissedAt ? query : null;
  const candidates: CrmUser[] = open ? filterMentionCandidates(activeUsers, open.query) : [];
  const menuOpen = open !== null && candidates.length > 0;
  const key = open ? `${open.start}:${open.query}` : '';
  const index = highlight.key === key ? Math.min(highlight.index, candidates.length - 1) : 0;

  // Put the caret after an inserted mention once React has written the new value.
  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (el && pendingCaret.current !== null) {
      el.focus();
      el.setSelectionRange(pendingCaret.current, pendingCaret.current);
      pendingCaret.current = null;
    }
  }, [text, textareaRef]);

  const track = useCallback((el: HTMLTextAreaElement) => {
    const at = el.selectionStart === el.selectionEnd ? el.selectionStart : null;
    setCaret(at);
    // Leaving the dismissed `@` re-arms it.
    const q = at === null ? null : activeMentionQuery(el.value, at);
    if (!q || q.start !== dismissedAt) setDismissedAt(null);
  }, [dismissedAt]);

  // Plain functions, not useCallback: they close over this render's query and candidates,
  // which change on every keystroke anyway.
  const choose = (user: CrmUser) => {
    if (!open || caret === null) return;
    const label = mentionLabel(user);
    const next = applyMention(text, open.start, caret, label);
    pendingCaret.current = next.caret;
    setCaret(next.caret);
    setPicked(prev => (prev.some(p => p.id === user.id) ? prev : [...prev, { id: user.id, label }]));
    setText(next.text);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>): boolean => {
    if (!menuOpen || !open) return false;
    // An IME is mid-composition: this key belongs to the candidate being typed.
    if (e.nativeEvent.isComposing) return false;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const step = e.key === 'ArrowDown' ? 1 : -1;
      setHighlight({ key, index: (index + step + candidates.length) % candidates.length });
      return true;
    }
    if ((e.key === 'Enter' || e.key === 'Tab') && !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey) {
      e.preventDefault();
      choose(candidates[index]);
      return true;
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      setDismissedAt(open.start);
      return true;
    }
    return false;
  };

  const activeId = menuOpen ? `${listId}-opt-${index}` : undefined;
  const menu = menuOpen ? (
    <ul
      id={listId}
      role="listbox"
      aria-label="Mention a teammate"
      style={{
        position: 'absolute', left: 8, top: '100%', marginTop: 2, zIndex: 40,
        minWidth: 220, maxWidth: 320, listStyle: 'none', padding: 4, margin: 0,
        background: BG_ELEV, border: `1px solid ${LINE_STRONG}`, borderRadius: 6,
        boxShadow: `0 8px 40px ${SHADOW}`,
      }}
    >
      {candidates.map((user, i) => (
        <li
          key={user.id}
          id={`${listId}-opt-${i}`}
          role="option"
          aria-selected={i === index}
          // mousedown, not click: keep focus in the textarea so the caret survives.
          onMouseDown={e => { e.preventDefault(); choose(user); }}
          onMouseEnter={() => setHighlight({ key, index: i })}
          style={{
            padding: '6px 10px', borderRadius: 4, cursor: 'pointer', fontFamily: FONT_SANS,
            fontSize: 13, color: INK, background: i === index ? HOVER : 'transparent',
          }}
        >
          <span style={{ display: 'block' }}>{mentionLabel(user)}</span>
          {user.name.trim() && (
            <span style={{ display: 'block', fontSize: 11, color: INK_MUTE }}>{user.email}</span>
          )}
        </li>
      ))}
    </ul>
  ) : null;

  return {
    textareaProps: {
      onSelect: e => track(e.currentTarget),
      'aria-autocomplete': 'list',
      'aria-controls': menuOpen ? listId : undefined,
      'aria-activedescendant': activeId,
    },
    onKeyDown,
    onChange: track,
    menu,
    mentionIds: () => keptMentionIds(text, picked),
    reset: () => setPicked([]),
  };
}
