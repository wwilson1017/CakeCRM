import { confirmDialog } from '../confirm';
import type { DetailCloseReason } from './types';

/**
 * CRM's detail-panel close POLICY — which leave paths actually dismiss a panel.
 *
 * `CollectionDetail`'s DEFAULT policy allows every reason except `backdrop`, which would mean
 * Escape closes a CRM detail. CRM's does not, and did not before this migration: the deleted
 * `DetailPanelShell` hardcoded `closeOnEscape={false}` and its test pinned both that and the
 * backdrop refusal. Supplying `onRequestClose` replaces the layer default WHOLESALE, so the app
 * guard has to name both reasons — stating only `escape` would silently re-enable backdrop.
 *
 * A separate leaf module from `detailPrimitives.tsx` because that file exports COMPONENTS and
 * this exports functions: `react-refresh/only-export-components` is an error in this repo, and a
 * file may legally be one or the other, not both.
 */

/**
 * The reasons that actually close a CRM detail panel.
 *
 * Escape is deliberately still not one of them, even though this PR gives the edit forms real
 * dirty-close guards (which is the condition the old shell's docstring set for enabling it). The
 * blocker is a different one: CRM renders `QuickLogModal` / `QuickAddModal` / `LostReasonModal` /
 * `BulkUpdateModal` as plain `fixed inset-0` siblings with no Escape handling of their own, so an
 * Escape taken by the detail would close the panel UNDERNEATH an open modal and leave it orphaned
 * on screen. Enabling Escape means giving those four modals their own handling first.
 */
export const CRM_CLOSING_REASONS: readonly DetailCloseReason[] = ['button', 'nav'];

/** CRM's app-level close guard: × and ‹ › close, Escape and a backdrop click do not. */
export const denyEscapeBackdrop = (reason: DetailCloseReason): boolean =>
  CRM_CLOSING_REASONS.includes(reason);

/**
 * The body half of the contract, for a detail body holding unsaved work.
 *
 * It must NOT prompt for a reason the app guard is going to refuse anyway. `CollectionDetail`
 * asks the BODY guard first and only then the app guard, so an unconditional prompt would put
 * "Discard unsaved changes?" on screen for an Escape keypress and then decline to close whatever
 * the user answered — a dialog whose answer is ignored.
 *
 * It resolves through the app's own `confirmDialog` rather than `window.confirm`: the same detail
 * bodies already use it to confirm an activity delete, `DetailCloseGuard` accepts a promise, and
 * `CollectionDetail.request` already awaits the body guard under its one-in-flight lock, so a
 * second dismissal cannot queue up behind the open dialog. `ConfirmHost` renders above every
 * detail, takeover or centred.
 *
 * A body composes ALL of its dirty sources into the ONE `isDirty` this takes — `registerCloseGuard`
 * replaces rather than stacks, so a second registration would silently discard the first.
 *
 * The copy is deliberately entity-neutral: this module is shared by every collection detail, so
 * naming one record type here would ship the wrong sentence to the others.
 */
export async function confirmDiscardOn(
  reason: DetailCloseReason,
  isDirty: () => boolean,
): Promise<boolean> {
  if (!CRM_CLOSING_REASONS.includes(reason)) return true;
  if (!isDirty()) return true;
  return confirmDialog({
    title: 'Discard unsaved changes?',
    message: 'Your unsaved changes will be lost.',
    confirmLabel: 'Discard',
    danger: true,
  });
}
