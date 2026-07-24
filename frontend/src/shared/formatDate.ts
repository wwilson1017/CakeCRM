/**
 * Format a Postgres TIMESTAMPTZ ISO string as a short "Mon D, h:mm AM" label.
 * Shared by the CRM timelines (ActivityTimeline, NotesThread).
 */
export function formatDate(iso: string): string {
  try {
    // Postgres TIMESTAMPTZ already serializes with an offset (e.g. ...+00:00);
    // only append 'Z' for a bare naive string that carries no zone, so we never
    // produce an invalid "...+00:00Z".
    const hasZone = /[Zz]|[+-]\d\d:?\d\d$/.test(iso);
    const d = new Date(hasZone ? iso : iso + 'Z');
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) +
      ' ' + d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  } catch { return iso; }
}
