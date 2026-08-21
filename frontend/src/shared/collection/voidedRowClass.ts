// Strikethrough treatment for a voided (soft-deleted) row. The non-destructive standard renders
// voided records struck-through, never hidden, so the row stays auditable. Applied by the list
// and cards views only when a collection config supplies `getVoided`.

export const VOIDED_ROW_CLASS = 'line-through opacity-60';

export function voidedRowClass(voided: boolean): string {
  return voided ? VOIDED_ROW_CLASS : '';
}
