// @vitest-environment jsdom
//
// Two contracts, one panel:
//
//  • #71 — the assistant's name is a fixed brand. No input renders for it, and a save
//    never carries a `name` the server would ignore anyway.
//  • #106 — `PUT /api/assistant/identity` is `require_admin` while the GET is
//    member-legal, so a member must see the personality WITHOUT a Save that can only
//    403. Both directions are asserted: a missing gate and an over-eager one are the
//    same bug seen from opposite sides.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => vi.fn());
const useAuth = vi.hoisted(() => vi.fn());
vi.mock('../core/api/client', () => ({ api }));
vi.mock('../core/auth/AuthContext', () => ({ useAuth }));

const { IdentitySettings } = await import('./IdentitySettings');

const IDENTITY = { name: 'Baker', personality: 'Be terse.', using_default: false };

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  api.mockReset();
  api.mockResolvedValue(IDENTITY);
  useAuth.mockReturnValue({ isAdmin: true });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render(isAdmin: boolean) {
  useAuth.mockReturnValue({ isAdmin });
  await act(async () => root.render(<IdentitySettings onClose={() => {}} />));
}

function buttonLabelled(label: string): HTMLButtonElement | undefined {
  return [...container.querySelectorAll('button')].find(
    b => (b.textContent ?? '').trim() === label,
  ) as HTMLButtonElement | undefined;
}

function personality(): HTMLTextAreaElement {
  const el = container.querySelector('textarea');
  if (!el) throw new Error('personality textarea missing');
  return el as HTMLTextAreaElement;
}

describe('IdentitySettings — the name is a fixed brand (#71)', () => {
  it('renders the name as text, never as an input', async () => {
    await render(true);
    expect(container.querySelector('input')).toBeNull();
    expect(container.textContent).toContain('Baker');
  });

  it('saves the personality alone — no name in the request body', async () => {
    await render(true);
    const ta = personality();
    // React tracks the DOM value, so set it through the native setter before dispatching.
    const setValue = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype, 'value')!.set!;
    await act(async () => {
      setValue.call(ta, 'Be warm.');
      ta.dispatchEvent(new Event('input', { bubbles: true }));
    });
    api.mockResolvedValueOnce({ ...IDENTITY, personality: 'Be warm.' });
    await act(async () => { buttonLabelled('Save')!.click(); });

    const [url, init] = api.mock.calls.at(-1)!;
    expect(url).toBe('/api/assistant/identity');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(init.body)).toEqual({ personality: 'Be warm.' });
  });
});

describe('IdentitySettings — a failed load never becomes a destructive save', () => {
  it('renders no editor and no Save when the identity GET fails', async () => {
    // `draft` would still be '', which Save sends as "revert to the built-in default" —
    // so one click on a panel that never loaded would wipe a custom personality. Before
    // #71 the blank-name guard blocked that by accident; the guard is explicit now.
    api.mockRejectedValue(new Error('network'));
    await render(true);
    expect(container.querySelector('textarea')).toBeNull();
    expect(buttonLabelled('Save')).toBeUndefined();
    expect(container.textContent).toContain('Could not load');
  });
});

describe('IdentitySettings — only an admin may save (#106)', () => {
  it('gives an admin an editable personality with Save', async () => {
    await render(true);
    expect(buttonLabelled('Save')).toBeDefined();
    expect(buttonLabelled('Reset to default')).toBeDefined();
    expect(personality().readOnly).toBe(false);
  });

  it('gives a member the personality read-only, with no Save', async () => {
    await render(false);
    expect(buttonLabelled('Save')).toBeUndefined();
    expect(buttonLabelled('Reset to default')).toBeUndefined();
    expect(personality().readOnly).toBe(true);
    expect(personality().value).toBe('Be terse.');
    expect(container.textContent).toContain('Only an admin can change');
  });

  it('shows a member the built-in default text, not an empty box', async () => {
    // The GET resolves `personality` server-side, so "using the default" must still
    // render the text that is actually governing the assistant.
    api.mockResolvedValue({ name: 'Baker', personality: 'BUILT-IN', using_default: true });
    await render(false);
    expect(personality().value).toBe('BUILT-IN');
  });
});
