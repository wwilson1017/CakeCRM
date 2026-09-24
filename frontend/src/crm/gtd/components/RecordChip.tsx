import { Link } from 'react-router-dom';
import { isTodoPublicMode } from '../publicMode';
import type { Todo } from '../types';

/**
 * The CRM link chip: a todo attached to a contact or a deal names it.
 *
 * This is CakeCRM's answer to the blueprint's Projects-card chip. The blueprint had
 * to add link columns for it; here `todos` already carries contact_id/deal_id, so the
 * link is native and the chip is free.
 *
 * In the no-login public app the chip renders as plain text, never a link: those CRM
 * routes are behind the login the public surface deliberately does not have, so a
 * link there would dead-end a visitor at /login.
 */
export function RecordChip({ todo }: { todo: Todo }) {
  const label = todo.deal_title || todo.contact_name;
  if (!label) return null;
  const to = todo.deal_id ? `/crm/deals/${todo.deal_id}` : `/crm/contacts/${todo.contact_id}`;
  const className = 'rounded-full bg-sand px-2 py-0.5 text-muted';

  if (isTodoPublicMode) return <span className={className}>{label}</span>;
  return (
    <Link
      to={to}
      onClick={e => e.stopPropagation()}
      className={`${className} hover:text-charcoal underline-offset-2 hover:underline`}
    >
      {label}
    </Link>
  );
}
