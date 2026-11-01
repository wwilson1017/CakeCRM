export interface QuickUpdateBody {
  note?: string; value?: number; stage?: string; won?: boolean; closed_on?: string;
  lost?: boolean; lost_reason?: string; followup?: string;
}
export interface QuickUpdatePreview {
  deal: { id: number; title: string; stage: string; value: number | null;
    currency: string; company_name?: string | null; owner_id?: number | null };
  body: QuickUpdateBody; changes: string[]; needs: 'note'[]; warnings: string[];
}
export type TileKey = 'note' | 'value' | 'stage' | 'followup';
export type Tiles = Record<TileKey, boolean>;
export interface Fields { note: string; value: string; followup: string }
export function parseValueInput(raw: string): number | null {
  const text = raw.trim().replace(/[$,]/g, '');
  const value = Number(text);
  return text && Number.isFinite(value) && value >= 0 ? value : null;
}
export function buildBody(on: Tiles, fields: Fields, stage: string | null): QuickUpdateBody {
  const body: QuickUpdateBody = {};
  if (on.note && fields.note.trim()) body.note = fields.note.trim();
  const value = parseValueInput(fields.value);
  if (on.value && value !== null) body.value = value;
  if (on.stage && stage) body.stage = stage;
  if (on.followup && fields.followup.trim()) body.followup = fields.followup.trim();
  return body;
}
export function confirmBody(preview: QuickUpdatePreview, noteLine: string): QuickUpdateBody | null {
  if (preview.needs.includes('note')) {
    return noteLine.trim() ? { ...preview.body, note: noteLine.trim() } : null;
  }
  return preview.body;
}
export function closedSuffix(stage: string): string {
  return stage === 'won' ? ' · WON' : stage === 'lost' ? ' · LOST' : '';
}
export function valueLabel(value: number | null | undefined, currency: string): string {
  if (value == null) return 'no value';
  const amount = value.toLocaleString('en-US', { minimumFractionDigits: Number.isInteger(value) ? 0 : 2, maximumFractionDigits: 2 });
  return !currency || currency === 'USD' ? `$${amount}` : `${amount} ${currency}`;
}
export function stageLabel(stage: string): string { return stage.charAt(0).toUpperCase() + stage.slice(1); }
