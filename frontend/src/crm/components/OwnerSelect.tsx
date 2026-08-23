/**
 * OwnerSelect — the "Owner" picker shared by the four entity forms (issue #60).
 *
 * A plain <select>, because that is what the platform gives you for a short list of
 * mutually exclusive options and it already matches the other form fields.
 *
 * Two things it deliberately does:
 *
 * - Offers "Unassigned" as a real choice. NULL owner is a supported state, not an
 *   absence of data, and the user has to be able to get back to it.
 * - Offers only ACTIVE users, so you cannot newly assign work to a deactivated
 *   account — but if the record already belongs to a deactivated one, that person
 *   stays in the list, labelled, so opening the form doesn't silently reassign them.
 */

import { useUsers } from '../useUsers';
import { labelStyle, inputStyle } from '../../shared/styles';

const UNASSIGNED_VALUE = '';

export function OwnerSelect({
  value,
  onChange,
  label = 'Owner',
  id = 'owner-select',
}: {
  value: number | null;
  onChange: (ownerId: number | null) => void;
  label?: string;
  id?: string;
}) {
  const { users, activeUsers } = useUsers();

  // Keep the current owner selectable even when deactivated, or re-saving the form
  // would quietly move the record to whatever the select fell back to.
  const current = value === null ? undefined : users.find(u => u.id === value);
  const options = current && !current.is_active ? [...activeUsers, current] : activeUsers;

  return (
    <div>
      <label htmlFor={id} style={labelStyle}>{label}</label>
      <select
        id={id}
        value={value === null ? UNASSIGNED_VALUE : String(value)}
        onChange={e =>
          onChange(e.target.value === UNASSIGNED_VALUE ? null : Number(e.target.value))
        }
        style={inputStyle}
      >
        <option value={UNASSIGNED_VALUE}>Unassigned</option>
        {options.map(u => (
          <option key={u.id} value={String(u.id)}>
            {(u.name.trim() || u.email) + (u.is_active ? '' : ' (deactivated)')}
          </option>
        ))}
      </select>
    </div>
  );
}
