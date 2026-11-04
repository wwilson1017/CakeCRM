/**
 * Inbox triage layout: Compact (the default: the card fits without scrolling, Edit opens
 * the rest) or Expanded (notes, tags and project details right on the card).
 * Per device, in localStorage, independent of the signed-in account.
 */
import { useSyncExternalStore } from 'react';

export type TriageLayout = 'compact' | 'expanded';

export const TRIAGE_LAYOUT_KEY = 'cakecrm-triage-layout';

export function readTriageLayout(): TriageLayout {
  try {
    return localStorage.getItem(TRIAGE_LAYOUT_KEY) === 'expanded' ? 'expanded' : 'compact';
  } catch {
    return 'compact'; // storage blocked
  }
}

let chosen: TriageLayout | null = null;
const listeners = new Set<() => void>();

export function saveTriageLayout(layout: TriageLayout): void {
  try {
    if (layout === 'compact') localStorage.removeItem(TRIAGE_LAYOUT_KEY);
    else localStorage.setItem(TRIAGE_LAYOUT_KEY, layout);
  } catch {
    // Not remembered — still applied for this page.
  }
  chosen = layout;
  listeners.forEach(l => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function useTriageLayout(): [TriageLayout, (l: TriageLayout) => void] {
  return [useSyncExternalStore(subscribe, () => chosen ?? readTriageLayout()), saveTriageLayout];
}
