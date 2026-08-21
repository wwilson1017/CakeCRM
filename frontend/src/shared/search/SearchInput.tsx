import { useState, useEffect, useRef } from 'react';
import { IconSearch, IconX } from '../icons';
import { useDebounce } from '../hooks/useDebounce';

/** Every search box in the app settles at the same cadence. Not a prop until something needs a
 *  different one — a sibling surface's synchronous-filtering bar is the likely first caller. */
const DEBOUNCE_MS = 250;

interface Props {
  /** The settled query the page is filtering on. */
  value: string;
  /** Called with the settled query, after the debounce. */
  onChange: (value: string) => void;
  placeholder?: string;
  /** Accessible name. Defaults to the placeholder, which is the usual case. */
  ariaLabel?: string;
  /**
   * Bumped by the bar when the user clicks Clear, so the box empties even though the page's
   * settled query was ALREADY `''` (a facet or the owner pill was what made Clear visible).
   * Without it the local text survives the clear and the pending debounce re-applies it a
   * quarter-second later — the filter the user just dismissed, coming back on its own.
   */
  resetNonce?: number;
  className?: string;
}

/**
 * The shared search box, generalised from a sibling card-tracking surface's
 * `CardSearchInput`.
 *
 * The point of the component is that **typing state is local**. A controlled input wired
 * straight to the page re-renders the whole board on every keystroke; here the page only
 * hears the settled value, so a 2,000-row table re-renders once per query rather than once
 * per character.
 *
 * That makes it a two-way component, and the two directions are kept honest separately:
 *
 *  • **Up** — an effect keyed ONLY on the debounced text. It compares against the page's
 *    current value through a ref rather than a dependency, which is what makes an external
 *    change safe: when the page clears the box, the local text follows, the debounced value
 *    settles to `''`, the effect fires once, sees it already equals `value`, and reports
 *    nothing. Depending on `value` directly would instead re-run the effect mid-flight and
 *    push the stale in-flight text back up, undoing the clear.
 *  • **Down** — React's sanctioned adjust-state-during-render shape (a `prevValue` state
 *    variable, compared during render). Deliberately not a ref written during render, which
 *    is impure and double-fires under StrictMode.
 *
 * On mount the local text starts at `value`, so the debounced value already equals it and
 * nothing is reported — which is what protects a filter state restored from sessionStorage
 * from being immediately overwritten with a blank.
 */
export default function SearchInput({
  value,
  onChange,
  placeholder = 'Search...',
  ariaLabel,
  resetNonce = 0,
  className = '',
}: Props) {
  const [text, setText] = useState(value);
  const [prevValue, setPrevValue] = useState(value);
  const [prevNonce, setPrevNonce] = useState(resetNonce);

  // Adopt an external change to `value` (a restored state, a programmatic set) into the box.
  if (value !== prevValue) {
    setPrevValue(value);
    setText(value);
  }
  // …and adopt an explicit reset even when `value` did not change, which is the case a plain
  // value comparison cannot see.
  if (resetNonce !== prevNonce) {
    setPrevNonce(resetNonce);
    setText(value);
  }

  // Read the live props inside the reporting effect without making them dependencies —
  // see the class docstring for why `value` in particular must not be a dependency.
  const valueRef = useRef(value);
  const onChangeRef = useRef(onChange);
  useEffect(() => {
    valueRef.current = value;
    onChangeRef.current = onChange;
  });

  const debounced = useDebounce(text, DEBOUNCE_MS);
  useEffect(() => {
    if (debounced !== valueRef.current) onChangeRef.current(debounced);
  }, [debounced]);

  const handleChange = (next: string) => setText(next);

  return (
    <div className={`relative ${className}`}>
      <IconSearch className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted pointer-events-none" aria-hidden="true" />
      <input
        type="text"
        value={text}
        onChange={e => handleChange(e.target.value)}
        placeholder={placeholder}
        aria-label={ariaLabel ?? placeholder}
        className="w-full pl-8 pr-8 py-2 bg-cream border border-line rounded-lg text-sm text-charcoal placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand transition-colors"
      />
      {text !== '' && (
        <button
          type="button"
          onClick={() => handleChange('')}
          aria-label="Clear search"
          className="absolute right-2 top-1/2 -translate-y-1/2 w-5 h-5 inline-flex items-center justify-center rounded-full text-muted hover:text-charcoal hover:bg-sand transition-colors"
        >
          <IconX className="w-3.5 h-3.5" aria-hidden="true" />
        </button>
      )}
    </div>
  );
}
