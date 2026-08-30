import { useEffect, useId, useRef, useState } from 'react';
import { labelStyle, inputStyle, BG_ELEV, BG_RAISED, LINE_STRONG, SHADOW, INK, INK_MUTE, INK_DIM, ACCENT_TEXT, CORAL_TEXT, HOVER, FONT_SANS } from '../../shared/styles';
import { useDebounce } from '../../shared/hooks/useDebounce';

/**
 * A type-to-search picker for a linked CRM record, with inline quick-create (issue #123).
 *
 * Replaces the "fetch the first 200 rows into a <select>" pattern the entity forms used.
 * That pattern had a standing hazard the forms each hand-patched: a linked record outside
 * the capped alphabetical page has no <option>, so the control renders BLANK — which reads
 * as "no company" rather than "we could not find it". DealForm carried an explicit append
 * guard for the deal's own company and none for its contact. Here the selected record's
 * LABEL is a prop, so an out-of-page link displays correctly by construction and both
 * guards disappear.
 *
 * Deliberately not built on `shared/search`: `SearchInput` is a plain debounced text input
 * with no listbox, and that module documents itself (in `shared/search/index.ts`) as
 * client-side-only over an already-loaded dataset, explicitly disclaiming server-paginated
 * CRM contacts and companies.
 * The popover mechanics (click-outside + Escape) follow `PipelineFilterBar`'s FacetButton,
 * in the inline-style idiom the entity forms already use.
 *
 * Generic over the record type and parameterized by its callbacks, so the same component
 * serves the contact picker, the company picker, and (issue #126) ContactForm's company
 * field with no changes here.
 */
interface Props<T> {
  label: string;
  /** The linked record's id, or null for "not linked". */
  value: number | null;
  /**
   * The linked record's display name. A prop rather than something looked up from
   * `results`, because the linked record is frequently NOT in any page of results — that
   * is the whole failure this component exists to end.
   */
  valueLabel: string;
  /** Shown when nothing is linked — e.g. "No contact". */
  emptyLabel: string;
  /** Query the server. Called with the TRIMMED query; "" means "first page, unfiltered". */
  search: (query: string) => Promise<T[]>;
  /** Create a record from a typed name and return it. Called with the TRIMMED name. */
  create: (name: string) => Promise<T>;
  getId: (record: T) => number;
  getLabel: (record: T) => string;
  /**
   * The record's canonical NAME, when that differs from its display label. Matching must
   * not see decoration: the company picker labels an archived row "Acme (archived)", and
   * comparing that against a typed "Acme" reports no exact match — so the list offers
   * `Create "Acme"…` for a company sitting directly above it. Defaults to `getLabel`.
   */
  getMatchText?: (record: T) => string;
  /** Optional second line on a row (a contact's company, a company's domain). */
  getSublabel?: (record: T) => string;
  /** Receives the whole record so a caller can read its other fields; null = unlinked. */
  onSelect: (record: T | null) => void;
  /**
   * Notified while a quick-create is in flight, so the surrounding form can refuse to submit
   * underneath it. Closing this widget deliberately does NOT abandon the create (see
   * `dismiss`), which means the link can still arrive a moment later — and a form that
   * submitted in the meantime would save without it while the record was written anyway.
   * Must be referentially stable (a `useState` setter is).
   */
  onBusyChange?: (busy: boolean) => void;
  id?: string;
}

export function RecordCombobox<T>({
  label, value, valueLabel, emptyLabel, search, create,
  getId, getLabel, getMatchText, getSublabel, onSelect, onBusyChange, id,
}: Props<T>) {
  const matchTextOf = getMatchText ?? getLabel;
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  // The last settled search, tagged with the query it answered. One state instead of a
  // `results` + `loading` pair, for two reasons: it keeps the fetch effect free of a
  // synchronous setState (which this repo's react-hooks v7 ruleset rejects, and which is
  // fixed by restructuring rather than suppressed), and it makes "these rows are for the
  // query on screen" checkable instead of assumed.
  const [settled, setSettled] = useState<{ query: string; rows: T[]; failed: boolean } | null>(null);
  // The name the IN-FLIGHT create was called with, not whatever is in the box now: the input
  // stays editable while a create runs, so rendering `trimmed` made the row claim to be
  // creating a name nothing was creating.
  const [creatingName, setCreatingName] = useState<string | null>(null);
  const creating = creatingName !== null;
  const [navigated, setNavigated] = useState(false);
  const [error, setError] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const wrapRef = useRef<HTMLDivElement>(null);
  // Monotonic request id: a slow earlier search must not overwrite a newer one's results.
  // Without it, typing "ac" then "acme" can settle in the wrong order and leave the list
  // showing matches for a query the user has already moved past.
  const reqRef = useRef(0);
  // Bumped by every selection and dismissal, so an in-flight quick-create can tell whether
  // the user is still waiting for it.
  const intentRef = useRef(0);
  const listId = useId();
  // Falls back to a generated id rather than rendering `undefined`: the `id` prop is
  // optional, and without this a caller that omits it (#126's ContactForm is the next one)
  // silently loses both click-to-focus on the label and the input's accessible name.
  const inputId = id ?? `${listId}-input`;
  const debounced = useDebounce(query, 250);

  // The trimmed query is the single form used for BOTH searching and creating. Trimming
  // before the search is what closes the leading/trailing-whitespace duplicate hole for
  // free: " Acme Corp " searches as "Acme Corp", so an existing company surfaces as a
  // match and the Create row is never offered for it.
  const trimmed = query.trim();
  const pending = debounced.trim();
  // Rows count as current only when they answer the query the user can see. `loading` also
  // covers the debounce window, which is what stops the Create row from flashing against a
  // stale result set: typing "Acme" right after "Acm" returned nothing must not offer
  // `Create "Acme"` before the search for "Acme" has actually run.
  const fresh = settled !== null && settled.query === pending && pending === trimmed;
  const results = fresh ? settled.rows : [];
  const loading = open && !fresh;
  // A failed search is NOT the same as an empty one, and the difference is user-visible:
  // rendering "No matches" for a request that errored would state as fact the one thing we
  // do not know. It matters most for the CONTACT picker, whose create path has no
  // uniqueness constraint behind it (the company path resolves through
  // uq_companies_name_ci and dedupes regardless of what the client believed) — so a
  // transient failure silently inviting `Create "…"` is how a duplicate contact gets made.
  // Create stays available: refusing it would strand a user whose search backend is down.
  const searchFailed = fresh && settled.failed;
  const exactMatch = results.some(r => matchTextOf(r).trim().toLowerCase() === trimmed.toLowerCase());
  const canCreate = trimmed !== '' && !loading && !exactMatch;
  // The row also stays up while a create is in flight, even when `canCreate` has gone false
  // because the user kept typing (a new query is `loading`, which suppresses it). Otherwise
  // the one piece of feedback that a record IS being written vanishes mid-request.
  const showCreate = canCreate || creating;
  const createIndex = results.length;
  const rowCount = results.length + (showCreate ? 1 : 0);
  // Clamped at the point of use rather than corrected in an effect: the row count shrinks
  // whenever a narrower search lands, and a stale index would otherwise point past the end.
  const active = rowCount === 0 ? 0 : Math.min(activeIndex, rowCount - 1);

  useEffect(() => {
    if (!open) return;
    // Wait for the debounce to catch up with what is on screen. `useDebounce` cannot be
    // reset, so right after `openList` clears the query the debounced value still holds the
    // PREVIOUS text for 250ms — without this, reopening the picker fires a search for a
    // query the user can no longer see, and only then the empty one they asked for.
    if (debounced.trim() !== trimmed) return;
    const seq = ++reqRef.current;
    let cancelled = false;
    const q = debounced.trim();
    const land = (rows: T[], failed: boolean) => {
      if (cancelled || seq !== reqRef.current) return;
      setSettled({ query: q, rows, failed });
      setActiveIndex(0);
    };
    // A failed search lands as zero rows rather than an error: the user is mid-form and
    // can still type a name and create it, which is the more useful outcome than a dead
    // list. It is FLAGGED as failed, though, so the list says so instead of claiming there
    // was nothing to find. A failed CREATE surfaces its own message, because there the
    // record the user asked for silently would not exist.
    search(q).then(rows => land(rows, false), () => land([], true));
    return () => { cancelled = true; };
  }, [open, debounced, trimmed, search]);

  useEffect(() => { onBusyChange?.(creating); }, [creating, onBusyChange]);

  // Keep the active row visible. Focus never leaves the input — only `aria-activedescendant`
  // moves — so the browser will not scroll the list on its own, and arrowing past the tenth
  // of twenty results would highlight a row nobody can see while Enter still picks it.
  useEffect(() => {
    if (!open) return;
    // Optional-called: jsdom does not implement scrollIntoView, and a picker must not throw
    // in a test run just to stay tidy on screen.
    document.getElementById(`${listId}-${active}`)?.scrollIntoView?.({ block: 'nearest' });
  }, [open, active, listId]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) dismiss();
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  function openList() {
    if (open) return;
    setQuery('');
    setSettled(null);
    setError('');
    setNavigated(false);
    setActiveIndex(0);
    setOpen(true);
  }

  /**
   * Close the popover, WITHOUT abandoning an in-flight create.
   *
   * The distinction is load-bearing and was originally missed: closing is not the same as
   * changing your mind. A click on the form's own Save button is a click "outside" this
   * widget, so treating every dismissal as abandonment meant the create the user had just
   * asked for was discarded while the record was still being written server-side — the deal
   * saved unlinked, and an orphan contact or company was left behind. That is the acceptance
   * flow of this feature, performed quickly.
   */
  function dismiss() {
    setOpen(false);
  }

  /** Close AND abandon an in-flight create — an explicit "never mind". */
  function cancel() {
    intentRef.current++;
    setOpen(false);
  }

  function choose(record: T) {
    intentRef.current++;
    onSelect(record);
    setOpen(false);
    setQuery('');
    setError('');   // a prior create's failure is not news about the record just chosen
  }

  /**
   * Unlink, and supersede any in-flight create while doing it.
   *
   * Clearing is a CHOICE about this field, so it belongs with `choose`, typing and Escape
   * rather than with passive dismissal. The × is reachable during a create — dismissing the
   * popover reveals it over the existing selection — and without the bump a create started
   * moments earlier would still pass its intent check and fill the field the user just
   * emptied.
   */
  function clearSelection() {
    intentRef.current++;
    setError('');
    onSelect(null);
  }

  async function quickCreate() {
    if (!trimmed || creating) return;
    // The search's request-id guard does not cover creates. Without this, a slow create
    // still calls `choose` after the user has dismissed the list, cleared the field or
    // picked an existing row — silently replacing the choice they actually made.
    const intent = ++intentRef.current;
    setCreatingName(trimmed);
    setError('');
    try {
      const record = await create(trimmed);
      if (intentRef.current !== intent) return;  // superseded while the request was open
      choose(record);
    } catch (err: unknown) {
      // Kept inline rather than thrown: the user is mid-form, and the name they typed is
      // still in the box to correct. A toast would scroll away from the field it is about.
      // Intent-checked like the success path, or a failed create for "Alpha" reports itself
      // under the "Beta" the user has since typed.
      if (intentRef.current !== intent) return;
      setError(err instanceof Error ? err.message.replace(/^API error \d+: /, '') : 'Could not create');
    } finally {
      // `finally`, because the superseded branch above RETURNS: releasing the flag only on
      // the fall-through path would leave the row disabled and reading "Creating…" for the
      // rest of the form's life, every time a create was interrupted.
      setCreatingName(null);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    // An IME is mid-composition: this Enter commits the candidate the user is typing, not a
    // row in our list. Selecting on it would replace what they were writing — the failure is
    // routine for CJK input and invisible to anyone testing on a Latin keyboard.
    if (e.nativeEvent.isComposing) return;
    if (e.key === 'Escape') {
      // Closes the list only — the existing selection survives, so Escape is never a
      // destructive keystroke here.
      if (open) { e.preventDefault(); e.stopPropagation(); cancel(); }
      return;
    }
    if (e.key === 'Tab') {
      // Focus is leaving for the next field; the popover must not stay open over it. Handled
      // here rather than on blur, because a blur handler also fires when a click lands on an
      // option and would close the list before the click could select anything.
      if (open) dismiss();
      return;
    }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!open) { openList(); return; }
      if (rowCount === 0) return;
      const step = e.key === 'ArrowDown' ? 1 : -1;
      setNavigated(true);
      setActiveIndex((active + step + rowCount) % rowCount);
      return;
    }
    if (e.key === 'Enter') {
      // Always swallowed while the list is open. This input lives inside a <form>, so a
      // bare Enter would SUBMIT the deal instead of acting on the list.
      if (!open) return;
      e.preventDefault();
      // ...but swallowing it is not the same as SELECTING with it. The list opens on focus
      // and `active` has no "nothing highlighted" state, so without this an Enter typed out
      // of submit habit — after merely tabbing into the field of an already-linked deal —
      // would replace that link with whatever the unfiltered first page happened to sort
      // first. Enter selects only once the user has expressed intent about the list, by
      // typing or by arrowing (the ARIA combobox practice, and the reason `aria-activedescendant`
      // exists rather than a default highlight).
      if (trimmed === '' && !navigated) { dismiss(); return; }
      if (canCreate && active === createIndex) { void quickCreate(); return; }
      const record = results[active];
      if (record) choose(record);
    }
  }

  const rowBase: React.CSSProperties = {
    display: 'block', width: '100%', textAlign: 'left', border: 'none',
    padding: '7px 10px', borderRadius: 4, cursor: 'pointer', fontSize: 13,
    fontFamily: FONT_SANS, background: 'transparent', color: INK,
  };

  // Hover follows the POINTER, not the list moving underneath it. Keyboard navigation
  // scrolls the popover, which drags rows past a stationary cursor and fires plain
  // mouseenter on each — hijacking the active row mid-keystroke. A mousemove carrying no
  // movement is the list scrolling, not the user.
  const onRowHover = (i: number) => (e: React.MouseEvent) => {
    if (e.movementX !== 0 || e.movementY !== 0) setActiveIndex(i);
  };

  return (
    <div ref={wrapRef} style={{ position: 'relative' }}>
      <label style={labelStyle} htmlFor={inputId}>{label}</label>
      <div style={{ position: 'relative' }}>
        <input
          id={inputId}
          role="combobox"
          aria-expanded={open}
          // Only while the list is mounted: pointing at an absent id is a dangling
          // reference, which assistive tech and every a11y linter treat as an error.
          aria-controls={open ? listId : undefined}
          aria-haspopup="listbox"
          aria-autocomplete="list"
          aria-activedescendant={open && rowCount > 0 ? `${listId}-${active}` : undefined}
          autoComplete="off"
          value={open ? query : (value != null ? valueLabel : '')}
          placeholder={value != null ? valueLabel : emptyLabel}
          onChange={e => {
            // Typing supersedes an in-flight create too: the input stays enabled while one
            // runs, so without this a create for "Alpha" can land and select itself after
            // the user has moved on to typing "Beta".
            intentRef.current++;
            setQuery(e.target.value);
            setError('');
          }}
          onFocus={openList}
          onClick={openList}
          onKeyDown={onKeyDown}
          style={{ ...inputStyle, paddingRight: value != null ? 30 : undefined }}
        />
        {value != null && !open && (
          <button
            type="button"
            aria-label={`Clear ${label.toLowerCase()}`}
            onClick={clearSelection}
            style={{
              position: 'absolute', right: 4, top: '50%', transform: 'translateY(-50%)',
              border: 'none', background: 'transparent', color: INK_DIM,
              cursor: 'pointer', fontSize: 15, lineHeight: 1, padding: '2px 6px',
            }}
          >
            ×
          </button>
        )}
      </div>
      {error && <p style={{ color: CORAL_TEXT, fontSize: 11, margin: '4px 0 0' }}>{error}</p>}
      {open && (
        <div
          style={{
            position: 'absolute', left: 0, right: 0, top: '100%', marginTop: 4, zIndex: 40,
            background: BG_ELEV, border: `1px solid ${LINE_STRONG}`, borderRadius: 6,
            padding: 4, boxShadow: `0 8px 40px ${SHADOW}`,
            maxHeight: 240, overflowY: 'auto',
          }}
        >
          <ul id={listId} role="listbox" aria-label={label} style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {results.map((r, i) => {
              const isActive = i === active;
              const sub = getSublabel?.(r);
              return (
                <li
                  key={getId(r)}
                  role="option"
                  id={`${listId}-${i}`}
                  aria-selected={isActive}
                  onMouseMove={onRowHover(i)}
                  onClick={() => choose(r)}
                  style={{ ...rowBase, background: isActive ? HOVER : 'transparent' }}
                >
                  <span style={{ display: 'block' }}>{getLabel(r)}</span>
                  {sub && <span style={{ display: 'block', fontSize: 11, color: INK_MUTE }}>{sub}</span>}
                </li>
              );
            })}
            {showCreate && (
              <li
                role="option"
                id={`${listId}-${createIndex}`}
                aria-selected={active === createIndex}
                aria-disabled={creating}
                onMouseMove={onRowHover(createIndex)}
                onClick={() => { if (!creating) void quickCreate(); }}
                style={{
                  ...rowBase, color: ACCENT_TEXT, fontWeight: 500,
                  background: active === createIndex ? HOVER : 'transparent',
                  borderTop: results.length > 0 ? `1px solid ${LINE_STRONG}` : undefined,
                  borderRadius: results.length > 0 ? 0 : 4,
                  cursor: creating ? 'default' : 'pointer',
                }}
              >
                {creating ? `Creating "${creatingName}"…` : `Create "${trimmed}"…`}
              </li>
            )}
          </ul>
          {searchFailed && (
            <p style={{ margin: 0, padding: '7px 10px', fontSize: 12, color: CORAL_TEXT }}>
              Search failed — results may be incomplete.
            </p>
          )}
          {rowCount === 0 && !searchFailed && (
            <p style={{ margin: 0, padding: '7px 10px', fontSize: 12, color: INK_DIM, background: BG_RAISED, borderRadius: 4 }}>
              {loading ? 'Searching…' : 'No matches'}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
