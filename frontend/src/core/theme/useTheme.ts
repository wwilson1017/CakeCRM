import { useCallback, useEffect, useSyncExternalStore } from 'react';

type Theme = 'light' | 'dark';

/** Must match the anti-flash script in index.html, which applies the
 *  .dark class before React loads using the same localStorage key. */
const THEME_KEY = 'cakecrm_theme';

// Survives across hook instances so an explicit toggle still wins over OS
// changes for the rest of the session even when localStorage is blocked.
let explicitPreferenceThisSession = false;

// Every mounted hook instance subscribes here and re-reads the DOM class on
// notify, so simultaneous consumers (and other tabs) never drift.
const listeners = new Set<() => void>();

function subscribe(onStoreChange: () => void) {
  listeners.add(onStoreChange);
  return () => { listeners.delete(onStoreChange); };
}

function currentDomTheme(): Theme {
  // The DOM class is the source of truth: the anti-flash script seeds it from
  // the stored/OS preference before React loads, and applyTheme keeps it current.
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light';
}

function hasExplicitPreference(): boolean {
  if (explicitPreferenceThisSession) return true;
  try {
    const stored = localStorage.getItem(THEME_KEY);
    return stored === 'dark' || stored === 'light';
  } catch {
    return false;
  }
}

function applyTheme(next: Theme) {
  document.documentElement.classList.toggle('dark', next === 'dark');
  listeners.forEach((notify) => notify());
}

export function useTheme() {
  const theme = useSyncExternalStore(subscribe, currentDomTheme);

  useEffect(() => {
    // Cross-tab: another tab persisted a new theme.
    const onStorage = (e: StorageEvent) => {
      if (e.key !== THEME_KEY) return;
      if (e.newValue === 'dark' || e.newValue === 'light') applyTheme(e.newValue);
    };
    window.addEventListener('storage', onStorage);

    // Follow live OS theme changes (e.g. macOS auto dark mode at sunset) until
    // the user sets an explicit preference via the toggle.
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onMqChange = (e: MediaQueryListEvent) => {
      if (hasExplicitPreference()) return;
      applyTheme(e.matches ? 'dark' : 'light');
    };
    mq.addEventListener('change', onMqChange);

    return () => {
      window.removeEventListener('storage', onStorage);
      mq.removeEventListener('change', onMqChange);
    };
  }, []);

  const toggleTheme = useCallback(() => {
    const next: Theme = currentDomTheme() === 'dark' ? 'light' : 'dark';
    explicitPreferenceThisSession = true;
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      // storage blocked or full — theme still applies for this session
    }
    applyTheme(next);
  }, []);

  return { theme, toggleTheme };
}
