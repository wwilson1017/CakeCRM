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
 * The body half of the contract, for a detail body holding an unsaved edit form.
 *
 * It must NOT prompt for a reason the app guard is going to refuse anyway. `CollectionDetail`
 * asks the BODY guard first and only then the app guard, so an unconditional `window.confirm`
 * would put "Discard unsaved changes?" on screen for an Escape keypress and then decline to close
 * whatever the user answered — a dialog whose answer is ignored.
 */
export function confirmDiscardOn(reason: DetailCloseReason, isDirty: () => boolean): boolean {
  if (!CRM_CLOSING_REASONS.includes(reason)) return true;
  return !isDirty() || window.confirm('Discard unsaved changes?');
}
