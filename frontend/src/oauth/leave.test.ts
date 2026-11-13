import { describe, expect, it } from 'vitest';
import { isSafeRedirect } from './leave';

describe('isSafeRedirect', () => {
  it.each(['javascript:alert(1)', 'data:text/html,x', 'vbscript:x', 'file:///etc/passwd', 'blob:https://a/b', 'about:blank', 'not a url'])(
    'refuses %s',
    (u) => expect(isSafeRedirect(u)).toBe(false),
  );
  it.each(['https://a.example/cb', 'http://localhost:1/cb', 'cursor://anysphere.cursor-retrieval/oauth/callback'])(
    'allows %s',
    (u) => expect(isSafeRedirect(u)).toBe(true),
  );
});
