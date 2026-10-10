/**
 * stageCriteria — what it takes for a deal to belong in each pipeline stage (issues #74, #289).
 *
 * Shown as a hover peek on a board column's stage name, and pinned as a non-modal panel on a
 * click. Since #289 an install may replace any stage's text with its own, so the frontend no
 * longer bundles the copy: the board reads the MERGED set from the server once per mount, and
 * each entry says whether it is the `standard` text or the install's `custom` one.
 *
 * The standard copy lives in ONE place, `backend/crm/stage_criteria_standard.json`, which the
 * server merges and `stageCriteria.test.ts` pins (stage coverage, vertical-neutral wording).
 * Custom text is the install's own data and is never committed.
 */
import { api } from '../core/api/client';

export interface StageCriteria {
  summary: string;
  checklist: string[];
}

export interface StageCriteriaEntry extends StageCriteria {
  stage: string;
  source: 'standard' | 'custom';
}

export type StageCriteriaMap = Record<string, StageCriteriaEntry>;

const PATH = '/api/crm/stage-criteria';

export async function fetchStageCriteria(): Promise<StageCriteriaMap> {
  const list = await api<StageCriteriaEntry[]>(PATH);
  return Object.fromEntries(list.map(e => [e.stage, e]));
}

/** Admin only (the route is `require_admin`). Resolves to the saved, server-trimmed entry. */
export function saveStageCriteria(stage: string, body: StageCriteria): Promise<StageCriteriaEntry> {
  return api<StageCriteriaEntry>(`${PATH}/${encodeURIComponent(stage)}`, {
    method: 'PUT', body: JSON.stringify(body),
  });
}

/** Admin only. Deletes the override; resolves to the standard entry. */
export function resetStageCriteria(stage: string): Promise<StageCriteriaEntry> {
  return api<StageCriteriaEntry>(`${PATH}/${encodeURIComponent(stage)}`, { method: 'DELETE' });
}
