// @vitest-environment jsdom
//
// The Settings half of #124. Three things are worth a DOM test rather than a unit test of
// `pipelineBoard`:
//
//   1. The checkbox reflects STORED state on mount, in both directions. A card that always
//      renders unchecked looks identical to one whose preference is off.
//   2. A click writes BOTH keys. Writing only the durable preference is the failure this
//      feature's whole design turns on: `loadHiddenStages` honours a stored per-tab set over
//      the preference, and the board persists one on its first render, so a Settings write
//      that skipped the reconciliation would leave the toggle inert for anyone arriving from
//      the board. The unit suite pins that inside `saveShowClosedStages`; this pins that the
//      card actually calls it.
//   3. It renders as a real Settings card under its own registry id.
//
// The localStorage shim is the same one `pipelineBoard.test.ts` documents: Node's global
// `localStorage` getter stays undefined without `--localstorage-file` and shadows the one jsdom
// would install, so `window.localStorage` is that same undefined. `sessionStorage` is a working
// Node global, which is why only half the storage needs help. The shim is the environment,
// never the subject — every assertion drives the real card and the real storage functions.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { PipelineBoardCard } from './PipelineBoardCard';
import { loadHiddenStages, loadShowClosedStages } from '../pipelineBoard';

function installLocalStorage(): () => void {
  const store = new Map<string, string>();
  const shim = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => { store.set(k, String(v)); },
    removeItem: (k: string) => { store.delete(k); },
    clear: () => { store.clear(); },
    key: (i: number) => [...store.keys()][i] ?? null,
    get length() { return store.size; },
  };
  const prev = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', { value: shim, configurable: true, writable: true });
  return () => {
    if (prev) Object.defineProperty(globalThis, 'localStorage', prev);
    else delete (globalThis as { localStorage?: unknown }).localStorage;
  };
}

let container: HTMLDivElement;
let root: Root;
let restoreLocalStorage: () => void = () => {};

beforeEach(() => {
  restoreLocalStorage = installLocalStorage();
  sessionStorage.clear();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  restoreLocalStorage();
});

async function render(): Promise<void> {
  await act(async () => { root.render(<PipelineBoardCard isMobile={false} />); });
}

const box = () => container.querySelector<HTMLInputElement>('input[type="checkbox"]')!;

async function click(): Promise<void> {
  await act(async () => { box().click(); });
}

describe('PipelineBoardCard', () => {
  it('renders unchecked when nothing is stored — closed stages hidden is the default', async () => {
    await render();
    expect(box().checked).toBe(false);
  });

  it('renders checked when the preference is on', async () => {
    localStorage.setItem('cakecrm_pipeline_show_closed', 'true');
    await render();
    expect(box().checked).toBe(true);
  });

  it('turning it on writes the preference AND clears this tab, so the board follows', async () => {
    // A board visit already left a stored set behind; a stored set outranks the preference.
    sessionStorage.setItem('crm_pipeline_hidden_stages', JSON.stringify(['won', 'lost']));
    await render();

    await click();

    expect(box().checked).toBe(true);
    expect(loadShowClosedStages()).toBe(true);
    expect(loadHiddenStages().size).toBe(0);
  });

  it('turning it off puts the closed stages back, in both stores', async () => {
    localStorage.setItem('cakecrm_pipeline_show_closed', 'true');
    sessionStorage.setItem('crm_pipeline_hidden_stages', '[]');
    await render();
    expect(box().checked).toBe(true);

    await click();

    expect(box().checked).toBe(false);
    expect(loadShowClosedStages()).toBe(false);
    expect([...loadHiddenStages()].sort()).toEqual(['lost', 'won']);
  });

  it('leaves a stage the person hid on the board alone', async () => {
    sessionStorage.setItem('crm_pipeline_hidden_stages', JSON.stringify(['won', 'lost', 'proposal']));
    await render();

    await click();

    expect([...loadHiddenStages()]).toEqual(['proposal']);
  });

  it('is a labelled Settings card carrying its own registry id', async () => {
    await render();
    const section = container.querySelector('section')!;
    expect(section.id).toBe('pipeline_board');
    expect(section.querySelector('h2')!.textContent).toContain('Pipeline board');
    // The checkbox is inside its <label>, so the accessible name comes for free.
    expect(box().closest('label')!.textContent).toContain('Show Won and Lost stages');
  });
});
