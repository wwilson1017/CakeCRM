// @vitest-environment jsdom
//
// Where an assistant message's links go (issue #145).
//
// This is not cosmetic. The session token lives in sessionStorage, which is per-tab and
// which a `noopener` new tab does not inherit — so an in-app link opened in a new tab
// arrives with no session, bounces through /login, and lands on the dashboard with the
// deal id thrown away. #145 makes the assistant hand out in-app deal links constantly, so
// the drawer is the feature's primary surface and this is what makes it work at all.
//
// The external half must not regress in the process: an assistant message can carry a
// prompt-injected href, and those keep opening in a new tab with `noopener noreferrer`.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { MarkdownContent } from './MarkdownContent';

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

async function render(markdown: string) {
  await act(async () => {
    root.render(<MemoryRouter><MarkdownContent content={markdown} /></MemoryRouter>);
  });
}

function anchor(): HTMLAnchorElement {
  const a = container.querySelector('a');
  if (!a) throw new Error(`no link rendered; got: ${container.innerHTML}`);
  return a;
}

describe('MarkdownContent links', () => {
  it('keeps a deal deep link in this tab', async () => {
    await render('[Acme renewal](/crm/pipeline?deal=42)');
    expect(anchor().getAttribute('target')).toBeNull();
    // react-router resolves it against the router's basename, so assert the destination
    // rather than the literal attribute.
    expect(anchor().getAttribute('href')).toBe('/crm/pipeline?deal=42');
  });

  it('keeps an absolute link to this origin in this tab', async () => {
    // What `deal_url` emits once FRONTEND_URL is configured — the same page, spelled out.
    await render(`[Acme renewal](${window.location.origin}/crm/pipeline?deal=42)`);
    expect(anchor().getAttribute('target')).toBeNull();
    expect(anchor().getAttribute('href')).toBe('/crm/pipeline?deal=42');
  });

  it('still opens an external link in a new tab, with noopener', async () => {
    await render('[docs](https://example.com/guide)');
    expect(anchor().getAttribute('target')).toBe('_blank');
    expect(anchor().getAttribute('rel')).toContain('noopener');
    expect(anchor().getAttribute('href')).toBe('https://example.com/guide');
  });

  it('treats a protocol-relative link as external', async () => {
    // `//evil.com/x` starts with a slash, so a `startsWith('/')` test would route it
    // through the in-app branch — and an assistant message can carry an injected href.
    await render('[free money](//evil.com/x)');
    expect(anchor().getAttribute('target')).toBe('_blank');
    expect(anchor().getAttribute('href')).toBe('//evil.com/x');
  });

  it('treats a backslash-prefixed link as external', async () => {
    await render('[free money](/\\evil.com/x)');
    const href = anchor().getAttribute('href') ?? '';
    // Either it is refused as a destination or it is treated as external — what must never
    // happen is an in-app <Link> pointing off-origin.
    if (anchor().getAttribute('target') === null) {
      expect(href.startsWith('/\\')).toBe(false);
    } else {
      expect(anchor().getAttribute('rel')).toContain('noopener');
    }
  });
});
