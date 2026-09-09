import { INK, INK_DIM } from '../../shared/styles';

/**
 * A Weekly Touches bucket's owner, rendered for reading (issue #146).
 *
 * Shared by the card's rep rows and the drill-down page's header so the two can't drift,
 * the same reason `TouchDealRow` was lifted out — the label is the identity of the row a
 * user is comparing reps by.
 *
 * The rule it enforces is #128's: an unassigned owner RENDERS. NULL ownership is a real,
 * permanent state (the Gmail scan, the assistant and the CSV importer all produce it), so
 * blank would read as a rendering fault; the muted italic is what keeps the word
 * "Unassigned" from reading as somebody's name.
 *
 * Deliberately NOT `OwnerName`, which resolves an `owner_id` through `useUsers()`: these
 * labels are resolved SERVER-side (name → email → "User N", or "Unassigned"), and
 * re-resolving them from the roster here would give the same bucket two spellings
 * depending on which surface you were looking at.
 */
export function RepLabel({ name, unassigned }: { name: string; unassigned: boolean }) {
  return (
    <span style={{
      fontSize: 14,
      color: unassigned ? INK_DIM : INK,
      fontStyle: unassigned ? 'italic' : undefined,
      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
    }}>{name}</span>
  );
}
