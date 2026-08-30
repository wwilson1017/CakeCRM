import { useEffect, useId, useRef, useState } from 'react';
import { labelStyle, inputStyle, BG_ELEV, BG_RAISED, LINE_STRONG, SHADOW, INK, INK_MUTE, INK_DIM, ACCENT_TEXT, CORAL, HOVER, FONT_SANS } from '../../shared/styles';
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
 * with no listbox, and `match.ts` documents itself as client-side-only over an
 * already-loaded array, explicitly disclaiming server-paginated contacts and companies.
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
  /** Optional second line on a row (a contact's company, a company's domain). */
  getSublabel?: (record: T) => string;
  /** Receives the whole record so a caller can read its other fields; null = unlinked. */
  onSelect: (record: T | null) => void;
  id?: string;
}

export function RecordCombobox<T>({
  label, value, valueLabel, emptyLabel, search, create,
  getId, getLabel, getSublabel, onSelect, id,
}: Props<T>) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  // The last settled search, tagged with the query it answered. One state instead of a
  // `results` + `loading` pair, for two reasons: it keeps the fetch effect free of a
  // synchronous setState (which this repo's react-hooks v7 ruleset rejects, and which is
  // fixed by restructuring rather than suppressed), and it makes "these rows are for the
  // query on screen" checkable instead of assumed.
  const [settled, setSettled] = useState<{ query: string; rows: T[] } | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const wrapRef = useRef<HTMLDivElement>(null);
  // Monotonic request id: a slow earlier search must not overwrite a newer one's results.
  // Without it, typing "ac" then "acme" can settle in the wrong order and leave the list
  // showing matches for a query the user has already moved past.
  const reqRef = useRef(0);
  const listId = useId();
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
  const exactMatch = results.some(r => getLabel(r).trim().toLowerCase() === trimmed.toLowerCase());
  const canCreate = trimmed !== '' && !loading && !exactMatch;
  const createIndex = results.length;
  const rowCount = results.length + (canCreate ? 1 : 0);
  // Clamped at the point of use rather than corrected in an effect: the row count shrinks
  // whenever a narrower search lands, and a stale index would otherwise point past the end.
  const active = rowCount === 0 ? 0 : Math.min(activeIndex, rowCount - 1);

  useEffect(() => {
    if (!open) return;
    const seq = ++reqRef.current;
    let cancelled = false;
    const q = debounced.trim();
    const land = (rows: T[]) => {
      if (cancelled || seq !== reqRef.current) return;
      setSettled({ query: q, rows });
      setActiveIndex(0);
    };
    // A failed search lands as zero rows rather than an error: the user is mid-form and
    // can still type a name and create it, which is the more useful outcome than a dead
    // list. A failed CREATE does surface, because there the record silently would not exist.
    search(q).then(land, () => land([]));
    return () => { cancelled = true; };
  }, [open, debounced, search]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  function openList() {
    if (open) return;
    setQuery('');
    setSettled(null);
    setError('');
    setActiveIndex(0);
    setOpen(true);
  }

  function choose(record: T) {
    onSelect(record);
    setOpen(false);
    setQuery('');
  }

  async function quickCreate() {
    if (!trimmed || creating) return;
    setCreating(true);
    setError('');
    try {
      choose(await create(trimmed));
    } catch (err: unknown) {
      // Kept inline rather than thrown: the user is mid-form, and the name they typed is
      // still in the box to correct. A toast would scroll away from the field it is about.
      setError(err instanceof Error ? err.message.replace(/^API error \d+: /, '') : 'Could not create');
    }
    setCreating(false);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Escape') {
      // Closes the list only — the existing selection survives, so Escape is never a
      // destructive keystroke here.
      if (open) { e.preventDefault(); e.stopPropagation(); setOpen(false); }
      return;
    }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!open) { openList(); return; }
      if (rowCount === 0) return;
      const step = e.key === 'ArrowDown' ? 1 : -1;
      setActiveIndex((active + step + rowCount) % rowCount);
      return;
    }
    if (e.key === 'Enter') {
      // Always swallowed while the list is open. This input lives inside a <form>, so a
      // bare Enter would SUBMIT the deal instead of picking the row the user is looking
      // at — the list being open means Enter is about the list.
      if (!open) return;
      e.preventDefault();
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

  return (
    <div ref={wrapRef} style={{ position: 'relative' }}>
      <label style={labelStyle} htmlFor={id}>{label}</label>
      <div style={{ position: 'relative' }}>
        <input
          id={id}
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-haspopup="listbox"
          aria-autocomplete="list"
          aria-activedescendant={open && rowCount > 0 ? `${listId}-${active}` : undefined}
          autoComplete="off"
          value={open ? query : (value != null ? valueLabel : '')}
          placeholder={value != null ? valueLabel : emptyLabel}
          onChange={e => { setQuery(e.target.value); setError(''); }}
          onFocus={openList}
          onClick={openList}
          onKeyDown={onKeyDown}
          style={{ ...inputStyle, paddingRight: value != null ? 30 : undefined }}
        />
        {value != null && !open && (
          <button
            type="button"
            aria-label={`Clear ${label.toLowerCase()}`}
            onClick={() => onSelect(null)}
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
      {error && <p style={{ color: CORAL, fontSize: 11, margin: '4px 0 0' }}>{error}</p>}
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
                <li key={getId(r)} role="option" id={`${listId}-${i}`} aria-selected={isActive}>
                  <button
                    type="button"
                    tabIndex={-1}
                    onMouseEnter={() => setActiveIndex(i)}
                    onClick={() => choose(r)}
                    style={{ ...rowBase, background: isActive ? HOVER : 'transparent' }}
                  >
                    <span style={{ display: 'block' }}>{getLabel(r)}</span>
                    {sub && <span style={{ display: 'block', fontSize: 11, color: INK_MUTE }}>{sub}</span>}
                  </button>
                </li>
              );
            })}
            {canCreate && (
              <li role="option" id={`${listId}-${createIndex}`} aria-selected={active === createIndex}>
                <button
                  type="button"
                  tabIndex={-1}
                  disabled={creating}
                  onMouseEnter={() => setActiveIndex(createIndex)}
                  onClick={() => { void quickCreate(); }}
                  style={{
                    ...rowBase, color: ACCENT_TEXT, fontWeight: 500,
                    background: active === createIndex ? HOVER : 'transparent',
                    borderTop: results.length > 0 ? `1px solid ${LINE_STRONG}` : undefined,
                    borderRadius: results.length > 0 ? 0 : 4,
                  }}
                >
                  {creating ? `Creating "${trimmed}"…` : `Create "${trimmed}"…`}
                </button>
              </li>
            )}
          </ul>
          {rowCount === 0 && (
            <p style={{ margin: 0, padding: '7px 10px', fontSize: 12, color: INK_DIM, background: BG_RAISED, borderRadius: 4 }}>
              {loading ? 'Searching…' : 'No matches'}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
