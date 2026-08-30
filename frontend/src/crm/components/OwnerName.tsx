/**
 * A record's owner, rendered for reading (issue #128).
 *
 * Before this, `owner_id` was writable in the four entity forms and filterable through the
 * Owner facet, but no surface displayed it — you could not see who owned a deal or a contact
 * without opening Edit. The blueprint CRM had the weaker version of the same bug: its Owner
 * row rendered through a hide-when-blank primitive, so an unowned record showed no owner
 * line at all and a rep could not tell "unassigned" from "not displayed".
 *
 * So the rule this component exists to enforce is that an unassigned owner RENDERS. NULL is
 * a real, supported state (#60) — the Gmail scan, the assistant and the CSV importer all
 * legitimately produce it — and blank reads as a rendering fault. `useUsers().nameFor()`
 * already resolves NULL to "Unassigned"; the muted italic is what keeps that word from
 * reading as somebody's name.
 *
 * Callers must render it unconditionally. Wrapping it in `{owner_id && …}` reintroduces
 * exactly the bug.
 */

import { useUsers } from '../useUsers';
import { INK, INK_DIM } from '../../shared/styles';

export function OwnerName({ ownerId }: { ownerId?: number | null }) {
  const { nameFor } = useUsers();
  const unassigned = ownerId === null || ownerId === undefined;
  return (
    <span style={{
      fontSize: 13,
      color: unassigned ? INK_DIM : INK,
      fontStyle: unassigned ? 'italic' : undefined,
    }}>{nameFor(ownerId)}</span>
  );
}
