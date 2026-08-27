// @vitest-environment jsdom
//
// Object-URL lifetime (#57). This hook is the entire "never a bare <img src>" mechanism
// AND the place image memory is bounded, so both halves are pinned: every URL it creates
// is revoked exactly once, and it never hands a consumer a URL belonging to a path that is
// no longer current (which would render a revoked URL — a broken image with no error
// event).
import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiBlob = vi.fn();
vi.mock('../core/api/client', () => ({ apiBlob: (p: string) => apiBlob(p) }));

const { useAuthedBlobUrl } = await import('./useAuthedBlobUrl');

let created: string[] = [];
let revoked: string[] = [];
let container: HTMLDivElement;
let root: Root;
let seq = 0;

beforeEach(() => {
  created = [];
  revoked = [];
  seq = 0;
  apiBlob.mockReset();
  URL.createObjectURL = vi.fn(() => {
    const url = `blob:mock/${++seq}`;
    created.push(url);
    return url;
  });
  URL.revokeObjectURL = vi.fn((url: string) => { revoked.push(url); });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

/** Renders the hook and exposes a setter so a test can change the path. */
function Probe({ initial, onState }: {
  initial: string | null;
  onState: (s: { url: string | null; error: boolean }) => void;
}) {
  const [path, setPath] = useState(initial);
  const state = useAuthedBlobUrl(path);
  onState(state);
  // Expose the setter through the DOM so the test can drive it without a ref forwarder.
  return <button onClick={() => setPath(pathQueue.shift() ?? null)}>go</button>;
}

let pathQueue: (string | null)[] = [];

function render(initial: string | null) {
  const states: { url: string | null; error: boolean }[] = [];
  act(() => { root.render(<Probe initial={initial} onState={s => states.push(s)} />); });
  return {
    states,
    latest: () => states[states.length - 1],
    change: (next: string | null) => {
      pathQueue = [next];
      act(() => {
        container.querySelector('button')!.dispatchEvent(
          new MouseEvent('click', { bubbles: true }),
        );
      });
    },
  };
}

const settle = async () => { await act(async () => { await Promise.resolve(); }); };

describe('useAuthedBlobUrl', () => {
  it('fetches with auth and exposes an object URL', async () => {
    apiBlob.mockResolvedValue(new Blob(['a']));
    const h = render('/api/crm/chatter/attachments/1/thumb');
    await settle();
    expect(apiBlob).toHaveBeenCalledWith('/api/crm/chatter/attachments/1/thumb');
    expect(h.latest().url).toBe('blob:mock/1');
    expect(h.latest().error).toBe(false);
  });

  it('never fetches for a null path', async () => {
    const h = render(null);
    await settle();
    expect(apiBlob).not.toHaveBeenCalled();
    expect(h.latest()).toEqual({ url: null, error: false });
  });

  it('reports an error without an URL when the fetch fails', async () => {
    apiBlob.mockRejectedValue(new Error('404'));
    const h = render('/a');
    await settle();
    expect(h.latest()).toEqual({ url: null, error: true });
    expect(created).toEqual([]);
  });

  it('revokes on unmount', async () => {
    apiBlob.mockResolvedValue(new Blob(['a']));
    render('/a');
    await settle();
    expect(revoked).toEqual([]);
    act(() => root.unmount());
    expect(revoked).toEqual(['blob:mock/1']);
    // Re-create so afterEach's unmount is a no-op rather than a double unmount.
    root = createRoot(container);
  });

  it('never returns the OLD path url once the path has changed', async () => {
    // The bug this exists for: effect cleanup revokes A while state still holds A, so a
    // consumer renders a revoked URL until B resolves.
    apiBlob.mockResolvedValue(new Blob(['a']));
    const h = render('/a');
    await settle();
    expect(h.latest().url).toBe('blob:mock/1');

    let resolveB: (b: Blob) => void = () => {};
    apiBlob.mockReturnValue(new Promise<Blob>(r => { resolveB = r; }));
    h.change('/b');
    // A's URL is revoked immediately by the cleanup...
    expect(revoked).toEqual(['blob:mock/1']);
    // ...and the hook reports NOTHING rather than the revoked URL.
    expect(h.latest().url).toBeNull();

    await act(async () => { resolveB(new Blob(['b'])); await Promise.resolve(); });
    expect(h.latest().url).toBe('blob:mock/2');
  });

  it('clears to null when the path becomes null', async () => {
    apiBlob.mockResolvedValue(new Blob(['a']));
    const h = render('/a');
    await settle();
    h.change(null);
    expect(h.latest().url).toBeNull();
    expect(revoked).toEqual(['blob:mock/1']);
  });

  it('creates no URL at all for a response that arrives after the path moved on', async () => {
    // Creating one and revoking it "later" is a leak whenever later never comes, so the
    // staleness check has to happen BEFORE createObjectURL.
    let resolveA: (b: Blob) => void = () => {};
    apiBlob.mockReturnValueOnce(new Promise<Blob>(r => { resolveA = r; }));
    const h = render('/a');
    await settle();

    apiBlob.mockResolvedValue(new Blob(['b']));
    h.change('/b');
    await settle();
    expect(created).toEqual(['blob:mock/1']);          // only B's

    await act(async () => { resolveA(new Blob(['a'])); await Promise.resolve(); });
    expect(created).toEqual(['blob:mock/1']);          // A never made one
    expect(h.latest().url).toBe('blob:mock/1');
  });

  it('leaves a stale rejection alone', async () => {
    let rejectA: (e: Error) => void = () => {};
    apiBlob.mockReturnValueOnce(new Promise<Blob>((_, rej) => { rejectA = rej; }));
    const h = render('/a');
    await settle();

    apiBlob.mockResolvedValue(new Blob(['b']));
    h.change('/b');
    await settle();

    await act(async () => { rejectA(new Error('gone')); await Promise.resolve().catch(() => {}); });
    // B is fine; A's late failure must not paint an error over it.
    expect(h.latest()).toEqual({ url: 'blob:mock/1', error: false });
  });

  it('revokes every URL it creates exactly once', async () => {
    apiBlob.mockResolvedValue(new Blob(['x']));
    const h = render('/a');
    await settle();
    h.change('/b');
    await settle();
    act(() => root.unmount());
    root = createRoot(container);

    expect(created).toHaveLength(2);
    expect([...revoked].sort()).toEqual([...created].sort());
    expect(new Set(revoked).size).toBe(revoked.length);
  });
});
