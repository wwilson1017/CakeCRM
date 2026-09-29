// @vitest-environment jsdom
//
// The edit path of @-mentions (#235). An edit ALWAYS sends its mention list — the server
// reads a list as the new set and an omitted field as "keep" — so deleting an `@name`
// token un-mentions that person. The editor seeds its picks from the note's stored
// mentions, so a mention whose seat has since been deactivated (and so is absent from the
// active roster) survives an unrelated edit.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const calls: { path: string; init?: RequestInit }[] = [];
const note = {
  id: 5, entity_type: 'deal', entity_id: 3, message: '@Ada Lovelace and @Gone Person, see this',
  created_at: '2026-09-29T00:00:00Z', updated_at: null, archived: 0, attachments: [],
  mentions: [{ user_id: 1, name: 'Ada Lovelace' }, { user_id: 9, name: 'Gone Person' }],
};

vi.mock('../../core/api/client', () => ({
  api: vi.fn(async (path: string, init?: RequestInit) => {
    calls.push({ path, init });
    if (path === '/api/users') {
      // Seat 9 is deactivated: it is not offered by the picker.
      return { users: [
        { id: 1, email: 'ada@example.test', name: 'Ada Lovelace', role: 'admin', is_active: true },
        { id: 9, email: 'gone@example.test', name: 'Gone Person', role: 'member', is_active: false },
      ] };
    }
    if (path.startsWith('/api/crm/chatter/deal/3')) return { notes: [note] };
    return { ...note };
  }),
}));

const { NotesThread } = await import('./NotesThread');

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  calls.length = 0;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const button = (label: string) =>
  Array.from(container.querySelectorAll('button')).find(b => b.textContent === label)!;

async function openEditor() {
  await act(async () => { root.render(<NotesThread entityType="deal" entityId={3} />); });
  await act(async () => { await new Promise(r => setTimeout(r, 0)); });
  await act(async () => { button('Edit').click(); });
  return container.querySelector<HTMLTextAreaElement>('textarea[aria-label="Edit note"]')!;
}

function type(el: HTMLTextAreaElement, value: string) {
  act(() => {
    Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

const patchBody = () => {
  const c = calls.find(x => x.init?.method === 'PATCH');
  return c ? JSON.parse(String(c.init!.body)) : null;
};

describe('NotesThread mentions', () => {
  it('highlights the mentioned names in a posted note', async () => {
    await act(async () => { root.render(<NotesThread entityType="deal" entityId={3} />); });
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    const highlighted = Array.from(container.querySelectorAll('p span'))
      .filter(s => (s as HTMLElement).style.fontWeight === '600')
      .map(s => s.textContent);
    expect(highlighted).toEqual(['@Ada Lovelace', '@Gone Person']);
  });

  it('an unrelated edit keeps every stored mention, the deactivated one included', async () => {
    const box = await openEditor();
    type(box, '@Ada Lovelace and @Gone Person, see this — updated');
    await act(async () => { button('Save').click(); });
    expect(patchBody()).toEqual({
      message: '@Ada Lovelace and @Gone Person, see this — updated', mentions: [1, 9],
    });
  });

  it('deleting a token un-mentions that person, and the list is sent even when empty', async () => {
    const box = await openEditor();
    type(box, '@Ada Lovelace, see this');
    await act(async () => { button('Save').click(); });
    expect(patchBody()).toEqual({ message: '@Ada Lovelace, see this', mentions: [1] });

    calls.length = 0;
    const again = await openEditor();
    type(again, 'nobody now');
    await act(async () => { button('Save').click(); });
    expect(patchBody()).toEqual({ message: 'nobody now', mentions: [] });
  });
});
