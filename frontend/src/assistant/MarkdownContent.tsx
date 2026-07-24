// CakeCRM — markdown renderer for assistant messages.
// Light highlight.js theme (github.css) so code blocks match the light UI.

import 'highlight.js/styles/github.css';

import type { ComponentPropsWithoutRef } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import rehypeHighlight from 'rehype-highlight';
import remarkGfm from 'remark-gfm';

import { ACCENT, BG_RAISED, INK, INK_MUTE, LINE } from '../shared/styles';

const components: Components = {
  a: ({ href, children }: ComponentPropsWithoutRef<'a'>) => (
    <a href={href} target="_blank" rel="noopener noreferrer" style={{ color: ACCENT, textDecoration: 'underline' }}>
      {children}
    </a>
  ),
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
