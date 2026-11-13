/**
 * Copy-paste connect instructions per AI client for a personal token (#322). Each tool
 * spells its MCP config differently (Gemini CLI's `url` means SSE, so it needs `httpUrl`;
 * VS Code says `servers`, the rest `mcpServers`), so each gets its exact block.
 */

export const TOKEN_CLIENTS = ['Claude Code', 'Codex', 'Gemini CLI', 'Cursor', 'VS Code'] as const;
export type TokenClient = (typeof TOKEN_CLIENTS)[number];

export interface Snippet {
  /** Where it goes, in words. */
  where: string;
  code: string;
}

const json = (value: unknown) => JSON.stringify(value, null, 2);

export function tokenSnippet(client: TokenClient, url: string, token: string): Snippet {
  const headers = { Authorization: `Bearer ${token}` };
  switch (client) {
    case 'Claude Code':
      return {
        where: 'Run this in a terminal:',
        code: `claude mcp add --transport http cakecrm ${url} --header "Authorization: Bearer ${token}"`,
      };
    case 'Codex':
      return {
        where: 'Run this in a terminal, and keep the variable set where you start Codex:',
        code: `export CAKECRM_MCP_TOKEN=${token}\ncodex mcp add cakecrm --url ${url} --bearer-token-env-var CAKECRM_MCP_TOKEN`,
      };
    case 'Gemini CLI':
      return {
        where: 'Add this to ~/.gemini/settings.json:',
        code: json({ mcpServers: { cakecrm: { httpUrl: url, headers } } }),
      };
    case 'Cursor':
      return {
        where: 'Add this to ~/.cursor/mcp.json:',
        code: json({ mcpServers: { cakecrm: { url, headers } } }),
      };
    case 'VS Code':
      return {
        where: 'Add this to .vscode/mcp.json:',
        code: json({ servers: { cakecrm: { type: 'http', url, headers } } }),
      };
  }
}
