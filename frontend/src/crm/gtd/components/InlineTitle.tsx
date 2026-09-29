// Click-to-edit text (port of cake_os's `shared/inline-edit/InlineTitle`; #232 added the
// `body` variant). Stays GTD-local: the triage card and the project detail page are its
// only consumers, and a `shared/` promotion with one app reading it would be API nobody uses.
//
// Presentational on purpose: it owns the editing/draft state and hands a validated value
// back, so the CALLER keeps one busy/error surface for every write on its card.
//
// The `body` variant is the same control over a multi-line free-text field (a project's
// notes). The two differ in exactly three behaviours and share every hard part — the
// draft/closed/refocus state machine, the IME guard, the refuse-and-stay-open path and the
// post-refusal focus recovery — so a second component would have been this file copied to
// change an Enter handler.
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react';

/**
 * Which kind of field this is. It decides the three things a title and a body disagree about
 * and nothing else:
 *
 *   * `title` — Enter saves, and an emptied value is a slip that never reaches the caller.
 *   * `body` — Enter inserts a newline like any textarea, so blur and Escape are the only
 *     exits, and an emptied value is a legitimate save: clearing a notes field is something a
 *     user means, not a mistake, and a field that cannot be emptied is broken.
 */
type InlineEditVariant = 'title' | 'body';

/** Why the editor closed with nothing written — see `onCancel`. */
type InlineEditCancelReason = 'escape' | 'blank' | 'unchanged';

interface Props {
  title: string;
  /** Accessible name for the editor ("Todo title", "Project notes"). */
  label: string;
  disabled?: boolean;
  variant?: InlineEditVariant;
  /**
   * What the trigger shows while the value is empty, so an empty field is still something to
   * click. Without it an empty value renders a zero-width control nobody can hit — which is
   * why a project's notes were not rendered at all when blank before #232.
   */
  placeholder?: string;
  /**
   * Persist the edit; resolve false to refuse it. An unchanged value never reaches it, and
   * neither does a blank one unless `variant="body"`. On a refusal the editor STAYS OPEN with
   * the typed text, so the caller's failure line has something to explain.
   */
  onSave: (value: string) => Promise<boolean>;
  /**
   * The editor closed WITHOUT saving, and WHY. Exists so a caller holding a failure line can
   * clear it — and, for `'blank'`, explain a refusal the caller never sees the draft of. The
   * argument is optional to receive, so a zero-parameter handler still type-checks.
   */
  onCancel?: (reason: InlineEditCancelReason) => void;
  /** Typography of the static text — the editor mirrors it so nothing jumps. */
  className?: string;
}

export function InlineTitle({
  title, label, disabled = false, variant = 'title', placeholder, onSave, onCancel, className = '',
}: Props) {
  // The two variant-driven behaviours, named once so the branches below read as intent.
  const commitsOnEnter = variant === 'title';
  const allowEmpty = variant === 'body';
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const [saving, setSaving] = useState(false);
  // Escape and Enter both close the editor, and closing blurs it — this keeps the
  // trailing blur from re-running (or undoing) the decision already made.
  const closed = useRef(false);
  // Only a KEYBOARD finish returns focus to the trigger. On blur the user has already
  // clicked somewhere else, and stealing focus back mid-click can eat that click.
  const refocus = useRef(false);
  const triggerRef = useRef<HTMLSpanElement>(null);
  const editorRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);
  // Set when a save is REFUSED, consumed by the effect below.
  const recoverFocus = useRef(false);

  // Stable identity so React runs this only on mount; a per-render callback would
  // re-place the caret on every keystroke.
  const focusEnd = useCallback((el: HTMLInputElement | HTMLTextAreaElement | null) => {
    editorRef.current = el;
    if (!el) return;
    el.focus();
    // Caret at the end — clicking in to amend a title is the common case.
    el.setSelectionRange(el.value.length, el.value.length);
  }, []);

  useEffect(() => {
    if (editing || !refocus.current) return;
    refocus.current = false;
    triggerRef.current?.focus();
  }, [editing]);

  // Put focus BACK in the editor after a refused save. `saving` disables the field, and the
  // browser answers that by blurring it — so "stays open with the typed text" would otherwise
  // leave an open, unfocused editor whose next Escape reaches whatever owns the page's Escape
  // stack. An EFFECT rather than a `focus()` at the point of failure, because `setSaving(false)`
  // is not applied until React re-renders and focusing a still-disabled field is a silent no-op.
  useEffect(() => {
    if (!editing || saving || !recoverFocus.current) return;
    recoverFocus.current = false;
    editorRef.current?.focus();
  }, [editing, saving]);

  function start() {
    if (saving || disabled) return;
    setDraft(title);
    closed.current = false;
    setEditing(true);
  }

  async function finish(commit: boolean, keyboard = false) {
    if (closed.current) return;
    closed.current = true;
    // A TITLE is trimmed on the way out. A BODY is not: the field it edits stores free text
    // verbatim (`gtd_common.validate_notes` returns it untouched), so trimming would silently
    // drop the indent someone typed on the first line of a list, and every trailing newline.
    // A draft that is ENTIRELY whitespace still normalizes to empty, because a trigger
    // rendering three spaces is an invisible control and the placeholder is what makes an
    // empty field clickable.
    const next = commitsOnEnter || draft.trim() === '' ? draft.trim() : draft;
    // An emptied title is a slip, not a request — the API rejects it anyway. A `body` field is
    // the opposite: clearing notes is a real edit, so it commits.
    const blank = !next && !allowEmpty;
    // A body compares RAW for the same reason it saves raw — trimming one side only would make
    // an edit that adds a trailing newline compare equal and never save. Either way, opening
    // and blurring an EMPTY body field stays silent: "" === "" is unchanged.
    if (!commit || blank || next === (commitsOnEnter ? title.trim() : title)) {
      refocus.current = keyboard;
      setEditing(false);
      // Escape wins the label when both apply: the user asked to abandon the edit, so telling
      // them the draft they discarded was also blank would be noise.
      onCancel?.(!commit ? 'escape' : blank ? 'blank' : 'unchanged');
      return;
    }
    setSaving(true);
    const ok = await onSave(next);
    setSaving(false);
    if (ok) {
      refocus.current = keyboard;
      setEditing(false);
      return;
    }
    // The write was refused. Stay open with the text intact — discarding what the user just
    // typed and reverting to the old value loses their work — and ask for focus back once
    // React has re-enabled the field (see the effect).
    closed.current = false;
    recoverFocus.current = true;
  }

  if (editing) {
    const onKeyDown = (e: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      // Both keys carry the IME guard: Escape is how a Japanese / Chinese / Korean writer
      // dismisses an active conversion CANDIDATE, and the Enter that commits one reaches this
      // handler too — without the guard the first of either throws away the whole edit.
      if (e.nativeEvent.isComposing) return;
      // Claim ONLY the keys this editor acts on. A `body` field claims Escape alone: its Enter
      // is an ordinary newline, so the keystroke must reach the textarea's own default and
      // keep bubbling exactly as every other key in it does.
      if (e.key !== 'Escape' && !(e.key === 'Enter' && commitsOnEnter)) return;
      // The card and the page above it have their own Escape handling.
      e.stopPropagation();
      e.preventDefault();
      void finish(e.key !== 'Escape', true);
    };
    const editorCls = `${className} w-full min-w-0 rounded border border-brand bg-cream px-1 py-0.5 focus:outline-none`;
    // A title edits in an `<input>` (one line, the shape the triage card has always had); a
    // body edits in a `<textarea>`, since a newline is the one keystroke it exists to accept.
    return commitsOnEnter ? (
      <input
        ref={focusEnd}
        type="text"
        value={draft}
        disabled={saving}
        aria-label={label}
        onChange={e => setDraft(e.target.value)}
        onKeyDown={onKeyDown}
        onBlur={() => void finish(true)}
        onClick={e => e.stopPropagation()}
        className={editorCls}
      />
    ) : (
      <textarea
        ref={focusEnd}
        rows={4}
        value={draft}
        disabled={saving}
        aria-label={label}
        onChange={e => setDraft(e.target.value)}
        onKeyDown={onKeyDown}
        onBlur={() => void finish(true)}
        onClick={e => e.stopPropagation()}
        className={`${editorCls} resize-y`}
      />
    );
  }

  return (
    <span
      ref={triggerRef}
      role="button"
      tabIndex={0}
      // No tooltip while disabled — "Click to rename" on a control that cannot is a lie the
      // user only discovers by trying it.
      title={disabled ? undefined : commitsOnEnter ? 'Click to rename' : 'Click to edit'}
      // The visible text names this control, as it always has. The fallback is for the one
      // case that leaves nothing to read — an empty value with no placeholder. It is NOT
      // applied alongside a placeholder: the name must match the visible words (WCAG 2.5.3).
      aria-label={title || placeholder ? undefined : label}
      aria-disabled={disabled || undefined}
      onClick={start}
      onKeyDown={e => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        e.preventDefault();
        start();
      }}
      // `break-words` so a pasted URL wraps instead of widening the card, and
      // `min-w-0` so it can: this renders as a bare flex item on the triage
      // card, and `overflow-wrap: break-word` is explicitly NOT considered when
      // computing min-content size, so without this the item's automatic
      // minimum stays the full width of the URL and the wrap never engages.
      className={`${className} inline-block min-w-0 max-w-full cursor-text break-words rounded border border-transparent px-1 py-0.5 hover:border-line ${
        disabled ? 'opacity-50' : ''
      }`}
    >
      {title || (placeholder && <span className="italic opacity-80">{placeholder}</span>)}
    </span>
  );
}
