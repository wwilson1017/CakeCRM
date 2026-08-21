/**
 * Memory page API + types (issue #72 Phase 2).
 *
 * Two resource families behind one page: context FILES (`/api/context-files`) and
 * long-term FACTS (`/api/memory`). Both are keyless — plain rows and pure-SQL FTS — so
 * this page works fully with no AI provider configured.
 */

import { api } from '../../core/api/client';

export type ContextFileKind = 'soul' | 'memory' | 'topic' | 'daily';

export interface ContextFileMeta {
  id: number;
  filename: string;
  kind: ContextFileKind;
  headline: string;
  is_protected: boolean;
  written_by: string;
  size_chars: number;
  created_at: string;
  updated_at: string;
}

export interface ContextFile extends Omit<ContextFileMeta, 'size_chars'> {
  content: string;
  archived_at: string | null;
}

export interface MemoryFact {
  id: number;
  subject: string;
  predicate: string;
  object: string;
  memory_type: string | null;
  confidence: number | null;
  valid_from: string | null;
  valid_to: string | null;
  archived_at: string | null;
  retrieval_count: number | null;
  last_retrieved_at: string | null;
}

/** Filenames contain '/', so each segment must be encoded without encoding the separator. */
const encodePath = (filename: string) => filename.split('/').map(encodeURIComponent).join('/');

export const listContextFiles = () =>
  api<{ files: ContextFileMeta[] }>('/api/context-files').then((r) => r.files);

export const searchContextFiles = (q: string) =>
  api<{ results: ContextFileMeta[] }>(`/api/context-files/search?q=${encodeURIComponent(q)}`)
    .then((r) => r.results);

export const getContextFile = (filename: string) =>
  api<ContextFile>(`/api/context-files/file/${encodePath(filename)}`);

/**
 * `expectedUpdatedAt` is the optimistic-concurrency token: the value we loaded. The
 * server answers 409 if it no longer matches, so a save can never silently discard what
 * the assistant wrote while the editor was open.
 */
export const saveContextFile = (
  filename: string,
  content: string,
  expectedUpdatedAt: string | null,
) =>
  api<ContextFile>(`/api/context-files/file/${encodePath(filename)}`, {
    method: 'PUT',
    body: JSON.stringify({ content, expected_updated_at: expectedUpdatedAt }),
  });

export const deleteContextFile = (filename: string) =>
  api<{ deleted: boolean }>(`/api/context-files/file/${encodePath(filename)}`, {
    method: 'DELETE',
  });

export const listFacts = (search: string) =>
  api<{ facts: MemoryFact[] }>(
    search.trim()
      ? `/api/memory/facts/search?q=${encodeURIComponent(search.trim())}`
      : '/api/memory/facts',
  ).then((r) => r.facts);

export const deleteFact = (id: number) =>
  api<{ deleted: boolean }>(`/api/memory/facts/${id}`, { method: 'DELETE' });

export const invalidateFact = (id: number) =>
  api<{ ok: boolean }>(`/api/memory/facts/${id}/invalidate`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
