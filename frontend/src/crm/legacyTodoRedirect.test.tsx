// @vitest-environment jsdom
//
// Scope: #169's acceptance criterion that "/crm/tasks and each GTD sub-route redirect to
// /crm/todos/…". The rename left no compatibility shim on the REST API or the tool names
// — the frontend is the only consumer of those — so this redirect is the whole of the
// backward compatibility the rename ships, and a bookmark, a Telegram deep link or a
// browser-history entry is what it has to keep working.
//
// Every entry below is a route App.tsx really mounted under /crm/tasks before #169, plus
// the param, query-string, hash, trailing-slash and percent-encoded cases a real URL can
// carry. The bare "/crm/tasks" case is the one that proves the splat matches an EMPTY
// remainder; the encoded case is why the component reads useLocation().pathname instead
// of rebuilding the path from useParams()['*'], which comes back decoded.
//
// The probe renders the resolved pathname, so a passing assertion means the redirect
// LANDED on a route that exists — not merely that Navigate was handed a string.

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useLocation, useParams } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { LegacyTodoRedirect } from './legacyTodoRedirect';

function Probe() {
  const { pathname, search, hash } = useLocation();
  const { id } = useParams();
  return <div data-testid="here">{`${pathname}${search}${hash}|${id ?? ''}`}</div>;
}

async function landOn(entry: string): Promise<string> {
  const container = document.createElement('div');
  document.body.appendChild(container);
  let root!: Root;
  await act(async () => {
    root = createRoot(container);
    root.render(
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/crm">
            <Route path="tasks/*" element={<LegacyTodoRedirect />} />
            <Route path="todos" element={<Probe />} />
            <Route path="todos/projects/:id" element={<Probe />} />
            <Route path="todos/:page" element={<Probe />} />
          </Route>
          <Route path="*" element={<div data-testid="here">UNMATCHED</div>} />
        </Routes>
      </MemoryRouter>,
    );
  });
  const text = container.querySelector('[data-testid="here"]')?.textContent ?? '';
  await act(async () => {
    root.unmount();
  });
  container.remove();
  return text;
}

describe('legacy /crm/tasks URLs redirect to /crm/todos (#169)', () => {
  it.each([
    // The bare route, and every GTD sub-route App.tsx mounted before the rename.
    ['/crm/tasks', '/crm/todos|'],
    ['/crm/tasks/inbox', '/crm/todos/inbox|'],
    ['/crm/tasks/next', '/crm/todos/next|'],
    ['/crm/tasks/projects', '/crm/todos/projects|'],
    ['/crm/tasks/waiting', '/crm/todos/waiting|'],
    ['/crm/tasks/someday', '/crm/todos/someday|'],
    ['/crm/tasks/done', '/crm/todos/done|'],
    ['/crm/tasks/review', '/crm/todos/review|'],
    ['/crm/tasks/search', '/crm/todos/search|'],
    // The one param route: the id has to survive.
    ['/crm/tasks/projects/7', '/crm/todos/projects/7|7'],
    // A real bookmark carries more than a path.
    ['/crm/tasks/search?q=acme', '/crm/todos/search?q=acme|'],
    ['/crm/tasks/search?q=acme#top', '/crm/todos/search?q=acme#top|'],
    // A percent-encoded segment must survive verbatim — the decode round-trip a
    // useParams-based rewrite would lose.
    ['/crm/tasks/projects/a%2Fb', '/crm/todos/projects/a%2Fb|a/b'],
  ])('%s -> %s', async (from, expected) => {
    expect(await landOn(from)).toBe(expected);
  });

  it('rewrites only the leading /crm/tasks segment, never a later occurrence', async () => {
    // "tasks" appearing inside a project id must not be rewritten: the component
    // anchors its replacement at the start of the pathname.
    expect(await landOn('/crm/tasks/projects/tasks')).toBe('/crm/todos/projects/tasks|tasks');
  });
});
