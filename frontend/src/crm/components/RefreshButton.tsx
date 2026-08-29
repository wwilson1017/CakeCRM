/**
 * Re-sweep control for the corpus-loading list pages (issue #77).
 *
 * Before #77 these pages round-tripped to the server on every filter change, so another
 * user's — or the assistant's — writes appeared incidentally. A client-loaded corpus never
 * reloads on its own, which is the honest counterweight: one explicit control, and the
 * only way to see work done elsewhere without leaving the page.
 */
import { IconRefresh } from '../../shared/icons';
import { INK_MUTE, LINE, tint, INK } from '../../shared/styles';

export function RefreshButton({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        width: 30, height: 30, borderRadius: 4,
        border: `1px solid ${LINE}`, background: 'transparent',
        color: INK_MUTE, cursor: 'pointer',
      }}
      onMouseEnter={e => {
        (e.currentTarget as HTMLElement).style.background = tint(INK, 6);
        (e.currentTarget as HTMLElement).style.color = INK;
      }}
      onMouseLeave={e => {
        (e.currentTarget as HTMLElement).style.background = 'transparent';
        (e.currentTarget as HTMLElement).style.color = INK_MUTE;
      }}
    >
      <IconRefresh size={14} strokeWidth={1.85} />
    </button>
  );
}
