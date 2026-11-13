import { useSyncExternalStore } from 'react';
export const READ_ALOUD_KEY = 'cakecrm_read_aloud';
let fallback = false;
let memoryOnly = false;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach(l => l());
export function readAloudEnabled(): boolean {
  if (memoryOnly) return fallback;
  try { fallback = localStorage.getItem(READ_ALOUD_KEY) === '1'; } catch { /* blocked storage */ }
  return fallback;
}
export function setReadAloudEnabled(on: boolean): void {
  fallback = on;
  try { localStorage.setItem(READ_ALOUD_KEY, on ? '1' : '0'); } catch { memoryOnly = true; }
  notify();
}
function storageChanged(e: StorageEvent): void {
  if (e.key === READ_ALOUD_KEY || e.key === null) notify();
}
export function subscribeReadAloud(listener: () => void): () => void {
  if (!listeners.size && typeof window !== 'undefined') window.addEventListener('storage', storageChanged);
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (!listeners.size && typeof window !== 'undefined') window.removeEventListener('storage', storageChanged);
  };
}
export function useReadAloudEnabled(): [boolean, (on: boolean) => void] {
  return [useSyncExternalStore(subscribeReadAloud, readAloudEnabled, () => false), setReadAloudEnabled];
}
export function __resetReadAloudPrefsForTests(): void {
  fallback = false;
  memoryOnly = false;
  try { localStorage.removeItem(READ_ALOUD_KEY); } catch { /* blocked storage */ }
  notify();
}
