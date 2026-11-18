// #322: each AI client's exact connect block for a personal token.
import { describe, expect, it } from 'vitest';

import { TOKEN_CLIENTS, tokenSnippet } from './aiToolSnippets';

const URL = 'https://crm.example/mcp';

describe('tokenSnippet', () => {
  it('fills the address and token into every client\'s block', () => {
    for (const client of TOKEN_CLIENTS) {
      const { code } = tokenSnippet(client, URL, 'tok');
      expect(code).toContain(URL);
      expect(code).toContain('tok');
    }
  });

  it('uses httpUrl for Gemini CLI, whose url key means SSE', () => {
    expect(JSON.parse(tokenSnippet('Gemini CLI', URL, 't').code).mcpServers.cakecrm.httpUrl).toBe(URL);
  });

  it('uses servers with type http for VS Code', () => {
    expect(JSON.parse(tokenSnippet('VS Code', URL, 't').code).servers.cakecrm).toEqual({
      type: 'http', url: URL, headers: { Authorization: 'Bearer t' },
    });
  });

  it('keeps the Codex token in an environment variable', () => {
    expect(tokenSnippet('Codex', URL, 't').code).toContain('--bearer-token-env-var CAKECRM_MCP_TOKEN');
  });
});
