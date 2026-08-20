/** Shared "nothing here" panel for collection surfaces — the CRM EmptyState
 *  idiom: a dashed quiet box, never a bare blank area a user reads as a broken load. */
export default function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-dashed border-line bg-cream px-6 py-10 text-center text-sm text-muted">
      {message}
    </div>
  );
}
