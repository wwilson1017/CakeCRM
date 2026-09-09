// CakeCRM — markdown renderer for assistant messages.
// Light highlight.js theme (github.css) so code blocks match the light UI. It is a
// FIXED light theme, so index.css carries a `.dark .hljs*` override block — without it
// a fenced block renders as a white slab inside the dark drawer.

import 'highlight.js/styles/github.css';

import type { ComponentPropsWithoutRef } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import { Link } from 'react-router-dom';
import rehypeHighlight from 'rehype-highlight';
import remarkGfm from 'remark-gfm';

import { ACCENT_TEXT, BG_RAISED, INK, INK_MUTE, LINE } from '../shared/styles';

const linkStyle = { color: ACCENT_TEXT, textDecoration: 'underline' };

/**
 * The in-app destination a link points at, or null when it leaves this origin (#145).
 *
 * Parsed with `URL` rather than a `startsWith('/')` test on purpose: `//evil.com/x` also
 * starts with a slash and is a protocol-relative link to somewhere else entirely, and an
 * assistant message can carry a prompt-injected href. Comparing resolved origins is the
 * only check that cannot be talked around. A `javascript:` URL resolves to a null origin
 * and so is never internal (react-markdown's own url transform already drops those).
 */
function inAppPath(href: string | undefined): string | null {
  if (!href) return null;
  try {
    const url = new URL(href, window.location.origin);
    return url.origin === window.location.origin ? url.pathname + url.search + url.hash : null;
  } catch {
    return null;
  }
}

const components: Components = {
  // An in-app link navigates IN THIS TAB; everything else keeps opening in a new one with
  // `noopener noreferrer`.
  //
  // The tab matters, and it is the whole reason this is not one branch of an `<a>`: the
  // session token lives in sessionStorage, which is per-tab and which a `noopener` tab does
  // not inherit. So a deal link the assistant hands over in the drawer — the feature's
  // primary surface, added in #145 — used to open a tab with no session, bounce through
  // /login, and land on the dashboard with the deal id thrown away.
  a: ({ href, children }: ComponentPropsWithoutRef<'a'>) => {
    const internal = inAppPath(href);
    return internal !== null ? (
      <Link to={internal} style={linkStyle}>{children}</Link>
    ) : (
      <a href={href} target="_blank" rel="noopener noreferrer" style={linkStyle}>
        {children}
      </a>
    );
  },
  // Never fetch remote images: the browser would request the URL on render, which
  // a prompt-injected upload could use to exfiltrate CRM data in the query string
  // with zero clicks. Render the alt text instead (CRM tool results have no images).
  img: ({ alt }: ComponentPropsWithoutRef<'img'>) => (
    <span style={{ color: INK_MUTE, fontStyle: 'italic' }}>{alt ? `🖼️ ${alt}` : '🖼️ [image]'}</span>
  ),
  p: ({ children }) => <p style={{ margin: '0 0 8px' }}>{children}</p>,
  ul: ({ children }) => <ul style={{ margin: '0 0 8px', paddingLeft: 20 }}>{children}</ul>,
  ol: ({ children }) => <ol style={{ margin: '0 0 8px', paddingLeft: 20 }}>{children}</ol>,
  li: ({ children }) => <li style={{ margin: '2px 0' }}>{children}</li>,
  h1: ({ children }) => <h1 style={{ fontSize: 18, fontWeight: 600, margin: '10px 0 6px' }}>{children}</h1>,
  h2: ({ children }) => <h2 style={{ fontSize: 16, fontWeight: 600, margin: '10px 0 6px' }}>{children}</h2>,
  h3: ({ children }) => <h3 style={{ fontSize: 14, fontWeight: 600, margin: '8px 0 4px' }}>{children}</h3>,
  blockquote: ({ children }) => (
    <blockquote style={{ margin: '0 0 8px', paddingLeft: 12, borderLeft: `3px solid ${LINE}`, color: INK_MUTE }}>
      {children}
    </blockquote>
  ),
  pre: ({ children }) => (
    <pre style={{ background: BG_RAISED, padding: 12, borderRadius: 8, overflowX: 'auto', fontSize: 13, margin: '0 0 8px' }}>
      {children}
    </pre>
  ),
  code: ({ className, children }: ComponentPropsWithoutRef<'code'>) =>
    className ? (
      // fenced block: rehype-highlight added hljs classes; github.css styles it
      <code className={className}>{children}</code>
    ) : (
      <code style={{ background: BG_RAISED, borderRadius: 4, padding: '1px 5px', fontSize: 13 }}>{children}</code>
    ),
  table: ({ children }) => (
    <div style={{ overflowX: 'auto', margin: '0 0 8px' }}>
      <table style={{ borderCollapse: 'collapse', fontSize: 13 }}>{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th style={{ border: `1px solid ${LINE}`, padding: '4px 8px', textAlign: 'left', fontWeight: 600 }}>{children}</th>
  ),
  td: ({ children }) => <td style={{ border: `1px solid ${LINE}`, padding: '4px 8px' }}>{children}</td>,
};

export function MarkdownContent({ content }: { content: string }) {
  return (
    <div style={{ fontSize: 14, lineHeight: 1.55, color: INK, wordBreak: 'break-word' }}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
