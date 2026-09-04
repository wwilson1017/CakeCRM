import { useCallback, useRef, useState } from 'react';

interface Props {
  title: string;
  label: string;
  disabled?: boolean;
  /** Persist the new title. Resolve false to keep the old text on screen. */
  onSave: (title: string) => Promise<boolean>;
  /** Typography of the static text — the editor mirrors it so nothing jumps. */
  className?: string;
}

/**
 * Click-to-rename title. Editing happens in place: Enter or clicking away saves,
 * Escape reverts. The full editor stays available elsewhere for the fields that do
 * not fit on a row.
 */
export function InlineTitle({ title, label, disabled, onSave, className = '' }: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const [saving, setSaving] = useState(false);
  // Escape and Enter both close the editor, and closing blurs it — this keeps the
  // trailing blur from re-running (or undoing) the decision already made.
  const closed = useRef(false);

  // Stable identity so React runs this only on mount; a per-render callback would
  // re-place the caret on every keystroke.
  const focusEnd = useCallback((el: HTMLInputElement | null) => {
    if (!el) return;
    el.focus();
    // Caret at the end — clicking in to amend a title is the common case.
    el.setSelectionRange(el.value.length, el.value.length);
  }, []);

  function start() {
    if (saving || disabled) return;
    setDraft(title);
    closed.current = false;
    setEditing(true);
  }

  function finish(commit: boolean) {
    if (closed.current) return;
    closed.current = true;
    setEditing(false);
    const next = draft.trim();
    // An emptied title is a slip, not a request — the API rejects it anyway.
    if (commit && next && next !== title) void save(next);
  }

  async function save(next: string) {
    setSaving(true);
    await onSave(next);
    setSaving(false);
  }

  if (editing) {
    return (
      <input
        ref={focusEnd}
        type="text"
        value={draft}
        aria-label={label}
        onChange={e => setDraft(e.target.value)}
        onKeyDown={e => {
          e.stopPropagation();
          if (e.key === 'Escape') { e.preventDefault(); finish(false); }
          else if (e.key === 'Enter') { e.preventDefault(); finish(true); }
        }}
        onBlur={() => finish(true)}
        onClick={e => e.stopPropagation()}
        className={`${className} w-full min-w-0 rounded border border-brand bg-cream px-1 py-0.5 focus:outline-none`}
      />
    );
  }

  return (
    <span
      role="button"
      tabIndex={0}
      title="Click to rename"
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
      className={`${className} min-w-0 cursor-text break-words rounded border border-transparent px-1 py-0.5 hover:border-line ${
        saving ? 'opacity-50' : ''
      }`}
    >
      {title}
    </span>
  );
}
