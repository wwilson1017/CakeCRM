/**
 * Closed on (#279) — the pure rules the Mark Won dialog and the deal edit form share, kept
 * out of the component files so they test in Node.
 */

/** A typed value that may be sent: a whole `YYYY-MM-DD` that is not after `today`. */
export function closedOnIsValid(value: string, today: string): boolean {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && value <= today;
}

/** Why the edit form's Closed on cannot be saved, or `''`. Typing bypasses the input's `max`,
 *  and only leaving Won clears the date, so a blanked date is refused visibly rather than
 *  silently dropped from the patch. Only checked while the form's stage is Won. */
export function closedOnEditError(
  form: { stage: string; closed_on: string }, baseline: { closed_on: string }, today: string,
): string {
  if (form.stage !== 'won') return '';
  if (!form.closed_on) {
    return baseline.closed_on
      ? 'Closed on cannot be blank — move the deal out of Won to clear it.' : '';
  }
  return closedOnIsValid(form.closed_on, today) ? '' : 'Closed on cannot be in the future.';
}
