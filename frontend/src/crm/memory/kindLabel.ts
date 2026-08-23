/**
 * Presentation helpers for the Memory page (issue #72).
 *
 * Pure and separately tested — the page itself is mostly wiring, and these are the bits
 * where a wrong answer is actually visible to the user.
 */

import type { ContextFileKind, ContextFileMeta } from './api';

export const KIND_LABEL: Record<ContextFileKind, string> = {
  soul: 'Identity',
  memory: 'Snapshot',
  topic: 'Topic',
  daily: 'Daily note',
};

/** 'topics/pricing.md' → 'pricing'; 'daily/2026-08-21.md' → '2026-08-21'. */
export function displayName(filename: string): string {
  const base = filename.slice(filename.lastIndexOf('/') + 1);
  return base.endsWith('.md') ? base.slice(0, -3) : base;
}

/**
 * Who last wrote the file, in words. This is the visibility half of the #72 security
 * model: an unexpected `soul.md` rewrite should be obvious at a glance rather than
 * silent, so 'assistant' is always named explicitly.
 */
export function writerLabel(writtenBy: string): string {
  if (writtenBy === 'assistant') return 'Baker';
  if (writtenBy === 'user') return 'You';
  return 'Built-in';
}

/** Group order for the file list: identity first, then the snapshot, then the rest. */
const KIND_ORDER: Record<ContextFileKind, number> = { soul: 0, memory: 1, topic: 2, daily: 3 };

export function sortFiles(files: ContextFileMeta[]): ContextFileMeta[] {
  return [...files].sort((a, b) => {
    const byKind = KIND_ORDER[a.kind] - KIND_ORDER[b.kind];
    if (byKind !== 0) return byKind;
    // Daily notes read newest-first; topics are alphabetical, which is easier to scan
    // than a recency order that reshuffles every time Baker writes.
    if (a.kind === 'daily') return b.filename.localeCompare(a.filename);
    return a.filename.localeCompare(b.filename);
  });
}
