// @vitest-environment jsdom
//
// The one rule this component carries (issue #128): an unassigned owner RENDERS.
//
// NULL owner is a real, supported state (#60) — the Gmail scan, the assistant and the CSV
// importer all produce it — so a blank or absent owner line is indistinguishable from a
// rendering fault, which is the bug the blueprint had. The visual distinction (muted italic)
// is what stops "Unassigned" from reading as somebody's name.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => vi.fn());
vi.mock('../../core/api/client', () => ({ api }));

const { OwnerName } = await import('./OwnerName');
const { invalidateUsers } = await import('../useUsers');

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  api.mockReset();
  api.mockResolvedValue({
    users: [
      { id: 3, email: 'dana@example.test', name: 'Dana Reyes', role: 'member', is_active: true },
      { id: 4, email: 'sam@example.test', name: '  ', role: 'member', is_active: false },
    ],
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

/** useUsers holds a module-level cache shared across consumers — refill it per test. */
async function renderOwner(ownerId?: number | null) {
  await act(async () => { invalidateUsers(); });
  await act(async () => { root.render(<OwnerName ownerId={ownerId} />); });
  return container.querySelector('span')!;
}

describe('OwnerName', () => {
  it('renders "Unassigned" for a null owner', async () => {
    const el = await renderOwner(null);
    expect(el.textContent).toBe('Unassigned');
  });

  it('renders "Unassigned" for an ABSENT owner_id, not an empty node', async () => {
    // A payload from a pre-#60 backend omits the field entirely; `?? OWNER_UNASSIGNED`
    // elsewhere treats absent and null alike, and so must this.
    const el = await renderOwner(undefined);
    expect(el.textContent).toBe('Unassigned');
  });

  it('styles the unassigned state as muted italic so it does not read as a name', async () => {
    const el = await renderOwner(null);
    expect(el.style.fontStyle).toBe('italic');
  });

  it('renders the roster name for a known owner, upright', async () => {
    const el = await renderOwner(3);
    expect(el.textContent).toBe('Dana Reyes');
    expect(el.style.fontStyle).toBe('');
  });

  it('falls back to the email when a user has no name, and keeps departed users resolvable', async () => {
    // Inactive users stay in the roster on purpose — a departed rep still owns records.
    const el = await renderOwner(4);
    expect(el.textContent).toBe('sam@example.test');
  });

  it('renders an id-based placeholder rather than nothing for an unknown owner', async () => {
    const el = await renderOwner(99);
    expect(el.textContent).toBe('User 99');
  });
});
