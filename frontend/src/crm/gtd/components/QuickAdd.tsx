import { useMemo, useRef, useState } from 'react';
import { createTodo } from '../api';
import type { QuickAddChip } from '../quickAdd';
import { parseQuickAdd } from '../quickAdd';

interface Props {
  onAdded: () => void;
}

const CHIP_STYLES: Record<QuickAddChip['kind'], string> = {
  due: 'bg-blue-100 dark:bg-blue-950/40 text-blue-700 dark:text-blue-300',
  repeat: 'bg-purple-100 dark:bg-purple-950/40 text-purple-700 dark:text-purple-300',
  project: 'bg-sand text-muted',
  context: 'bg-sand text-muted',
  star: 'bg-amber-100 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300',
};

/**
 * One-line capture with live natural-language parsing. Recognized fragments render as
 * removable chips below the input BEFORE submit — visible parse feedback is what makes
 * natural-language capture trustworthy, because a wrong parse is then obvious and
 * harmless (dismissing a chip puts the words back in the title). Everything lands in
 * the Inbox; parsed fields ride along.
 */
export function QuickAdd({ onAdded }: Props) {
  const [input, setInput] = useState('');
  const [ignored, setIgnored] = useState<string[]>([]);
  const [autoStar, setAutoStar] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const parsed = useMemo(
    () => parseQuickAdd(input, new Date(), ignored),
    [input, ignored],
  );

  const submit = async () => {
    if (!parsed.title.trim() || busy) return;
    setBusy(true);
    setError('');
    try {
      await createTodo({
        title: parsed.title,
        status: 'inbox',
        project: parsed.project ?? null,
        context: parsed.context ?? '',
        star: parsed.star,
        due_date: parsed.due_date ?? '',
        repeat: parsed.repeat ?? '',
        auto_star_on_due: parsed.repeat ? autoStar : false,
      });
      setInput('');
      setIgnored([]);
      setAutoStar(false);
      onAdded();
      inputRef.current?.focus(); // rapid-fire capture
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to add');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <form onSubmit={e => { e.preventDefault(); void submit(); }} className="flex gap-2">
        <input
          ref={inputRef}
          value={input}
          onChange={e => { setInput(e.target.value); setIgnored([]); }}
          placeholder="Add to inbox — try “call Val tomorrow #Website @calls !”"
          aria-label="Add to inbox"
          className="min-w-0 flex-1 rounded-lg border border-line bg-cream px-3 py-2 text-base sm:text-sm text-charcoal placeholder:text-muted focus:border-brand focus:outline-none"
        />
        <button
          type="submit"
          disabled={!parsed.title.trim() || busy}
          className="shrink-0 rounded-lg bg-brand-dark px-4 py-2 text-sm font-heading text-white hover:bg-brand-deep disabled:opacity-40"
        >
          Add
        </button>
      </form>
      {parsed.chips.length > 0 && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          {parsed.chips.map((chip, i) => (
            <button
              key={`${chip.raw}-${i}`}
              type="button"
              onClick={() => setIgnored(prev => [...prev, chip.raw.trim()])}
              title="Remove — keeps the words in the title"
              className={`rounded-full px-2 py-0.5 text-xs ${CHIP_STYLES[chip.kind]}`}
            >
              {chip.label} ✕
            </button>
          ))}
        </div>
      )}
      {parsed.repeat && (
        <label className="mt-1.5 flex items-center gap-2 text-xs text-muted">
          <input
            type="checkbox"
            checked={autoStar}
            onChange={e => setAutoStar(e.target.checked)}
            className="h-3.5 w-3.5 accent-amber-500"
          />
          ★ Star the next occurrence when it comes due today
        </label>
      )}
      {error && <p className="mt-1 text-xs text-ck-accent-text">{error}</p>}
    </div>
  );
}
