// The phone bottom tab bar for the no-login todo app (#266, port of todo-gtd 614faa2).
//
// Public mode ONLY. Inside the CRM the GTD pages sit under `CrmLayout`, whose own
// mobile nav and the "Ask Baker" launcher already own the bottom of the screen, so
// `TodoShell` mounts this only when `isTodoPublicMode` is true. Below `sm` (640px) it
// replaces the shell's top tab strip; from `sm` up it is `display: none` and the strip
// is back. Both are CSS breakpoints rather than a media-query hook, so crossing the
// breakpoint never remounts the page under the user.
//
// The bar carries `ck-has-bottom-bar`, which `index.css` reads (`html:has(...)`, phone
// widths only) to set `--ck-bottom-bar` — the bar's height plus the safe-area inset.
// The page's bottom padding, scroll-padding, the undo block and the toast stack all
// clear the bar by reading that one variable.
import { useEffect, useId, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { todoPath } from '../publicMode';
import { TABS, type TabDef, type TodoTab } from '../tabs';

const byKey = (keys: TodoTab[]): TabDef[] => keys.map(k => TABS.find(t => t.key === k)!);

/** The four lists a phone works from, in the issue's order. */
const PRIMARY_TABS = byKey(['inbox', 'today', 'next', 'projects']);

/** Everything else, behind More. */
const MORE_TABS = byKey(['search', 'waiting', 'someday', 'review', 'done']);

const FOCUS = 'focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand';

// min-h-14 (56px) — over the 44px target floor; the 3px top border marks the current tab.
const tabCls = (current: boolean) =>
  `flex min-h-14 min-w-0 flex-1 items-center justify-center border-t-[3px] -mt-px px-0.5 text-xs ${FOCUS} ${
    current ? 'border-brand font-semibold text-charcoal' : 'border-transparent text-muted'
  }`;

interface Props {
  active: TodoTab;
  inboxCount: number;
}

export function BottomBar({ active, inboxCount }: Props) {
  return (
    <nav
      aria-label="Main lists"
      className="ck-has-bottom-bar fixed inset-x-0 bottom-0 z-40 has-[[aria-expanded=true]]:z-[110] flex border-t border-line bg-cream pb-[env(safe-area-inset-bottom)] sm:hidden"
    >
      {PRIMARY_TABS.map(item => {
        const current = item.key === active;
        return (
          <Link
            key={item.key}
            to={todoPath(item.sub)}
            aria-current={current ? 'page' : undefined}
            className={tabCls(current)}
          >
            {item.label}
            {item.key === 'inbox' && inboxCount > 0 && (
              <span className="ml-1 rounded-full bg-brand px-1.5 py-0.5 text-xs font-bold leading-none text-ck-accent-ink">
                {inboxCount}
              </span>
            )}
          </Link>
        );
      })}
      <MoreMenu active={active} />
    </nav>
  );
}

function MoreMenu({ active }: { active: TodoTab }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      setOpen(false);
      button.current?.focus();
    };
    const onDown = (e: PointerEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onDown);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('pointerdown', onDown);
    };
  }, [open]);

  // When the current page lives behind More, the button stands in for it in the bar.
  const holdsCurrent = MORE_TABS.some(i => i.key === active);

  return (
    <div ref={wrap} className="flex min-w-0 flex-1">
      <button
        ref={button}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen(o => !o)}
        className={tabCls(holdsCurrent)}
      >
        More
      </button>
      {open && (
        // The fixed nav is this panel's stacking context, so the panel's own z-index cannot
        // lift it over the undo block (z-[100]) — which rests just above the bar for 7s after
        // every completion, exactly when someone reaches for More next. The NAV rises to
        // z-[110] instead while More is open (`has-[[aria-expanded=true]]` above), so the
        // block never paints over, or takes the taps of, the menu's bottom rows.
        <div
          id={panelId}
          className="fixed inset-x-2 bottom-[calc(var(--ck-bottom-bar,3.5rem)+0.5rem)] rounded-xl border border-line bg-cream p-1.5 shadow-lg"
        >
          {MORE_TABS.map(item => {
            const current = item.key === active;
            return (
              <Link
                key={item.key}
                to={todoPath(item.sub)}
                aria-current={current ? 'page' : undefined}
                onClick={() => setOpen(false)}
                className={`block rounded-lg px-3 py-3 text-base text-charcoal hover:bg-sand ${FOCUS} ${
                  current ? 'bg-sand font-semibold' : ''
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
