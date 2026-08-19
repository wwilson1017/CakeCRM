import { useTheme } from '../../core/theme/useTheme';
import { INK_MUTE, LINE_STRONG } from '../../shared/styles';

/**
 * Light/dark switch. Ported from the CAKE OS blueprint's ThemeToggle and adapted to
 * CakeCRM's inline-style idiom, sized to match NotificationsBell in the CRM header.
 * The theme itself is entirely CSS — the hook only toggles `.dark` on <html>.
 */
export function ThemeToggle({ full }: { full?: boolean } = {}) {
  const { theme, toggleTheme } = useTheme();
  const label = theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';

  return (
    <button
      onClick={toggleTheme}
      aria-label={label}
      title={theme === 'dark' ? 'Light mode' : 'Dark mode'}
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
        width: full ? '100%' : 34, height: 32,
        border: `1px solid ${LINE_STRONG}`, borderRadius: 6,
        background: 'transparent', color: INK_MUTE, cursor: 'pointer',
        fontSize: 14,
      }}
    >
      <ThemeIcon theme={theme} />
      {full && <span>{theme === 'dark' ? 'Light mode' : 'Dark mode'}</span>}
    </button>
  );
}

function ThemeIcon({ theme }: { theme: 'light' | 'dark' }) {
  return theme === 'dark' ? (
    <svg aria-hidden="true" width="16" height="16" fill="none" viewBox="0 0 24 24"
         stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round"
            d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
    </svg>
  ) : (
    <svg aria-hidden="true" width="16" height="16" fill="none" viewBox="0 0 24 24"
         stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round"
            d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
    </svg>
  );
}
