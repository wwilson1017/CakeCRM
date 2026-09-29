// @vitest-environment jsdom
//
// The bell follows a notification's link (#235) only when it is an in-app path: a
// chatter @-mention's title opens its record, anything protocol-relative stays plain text.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const rows = [
  { id: 'a', title: 'Ada mentioned you on Deal — Acme', message: 'm', created_at: '2026-09-29T00:00:00Z', channels_sent: [], link: '/crm/pipeline?deal=12' },
  { id: 'b', title: 'Sneaky', message: 'm', created_at: '2026-09-29T00:00:00Z', channels_sent: [], link: '//evil.example/x' },
  { id: 'c', title: 'Digest', message: 'm', created_at: '2026-09-29T00:00:00Z', channels_sent: [], link: null },
];

vi.mock('../../core/api/client', () => ({
  api: vi.fn(async (path: string) => {
    if (path.startsWith('/api/notifications?')) return { notifications: rows };
    if (path.startsWith('/api/alerts?')) return { alerts: [] };
    return { count: rows.length };
  }),
}));

const { NotificationsBell } = await import('./NotificationsBell');

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function Where() {
  const loc = useLocation();
  return <output data-testid="where">{loc.pathname + loc.search}</output>;
}

describe('NotificationsBell links', () => {
  it('links an in-app path, leaves anything else as plain text, and closes on follow', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={['/crm']}>
          <NotificationsBell />
          <Routes><Route path="*" element={<Where />} /></Routes>
        </MemoryRouter>,
      );
    });
    await act(async () => {
      container.querySelector<HTMLButtonElement>('button[aria-label="Notifications"]')!.click();
    });
    await act(async () => { await Promise.resolve(); });

    const anchors = Array.from(container.querySelectorAll('a'));
    expect(anchors.map(a => a.textContent)).toEqual(['Ada mentioned you on Deal — Acme']);
    expect(container.textContent).toContain('Sneaky');
    expect(container.textContent).toContain('Digest');

    await act(async () => { anchors[0].click(); });
    expect(container.querySelector('[data-testid="where"]')!.textContent).toBe('/crm/pipeline?deal=12');
    expect(container.textContent).not.toContain('Sneaky');  // dropdown closed
  });
});
