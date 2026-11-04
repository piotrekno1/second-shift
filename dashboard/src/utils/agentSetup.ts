import { serverName, setupCommand } from './apiTokens'

export type AgentClient = 'claude' | 'codex' | 'cursor' | 'other'

export const agentClients: { label: string; value: AgentClient }[] = [
  { label: 'Claude Code', value: 'claude' },
  { label: 'Codex', value: 'codex' },
  { label: 'Cursor', value: 'cursor' },
  { label: 'Other client', value: 'other' },
]

interface AgentSetup {
  instruction: string
  content: string
  copyLabel: string
  nextStep: string
}

/** Personal setup only: token-bearing config must stay outside repository files. */
export function agentSetup(client: AgentClient, workspaceSlug: string, origin: string, token: string): AgentSetup {
  const url = `${origin.replace(/\/+$/, '')}/mcp`
  const name = serverName(workspaceSlug)
  if (client === 'claude') return {
    instruction: 'Run this command in your terminal.',
    content: setupCommand(workspaceSlug, origin, token),
    copyLabel: 'Copy command',
    nextStep: 'Restart Claude Code, then use /mcp to check the connection.',
  }
  if (client === 'codex') return {
    instruction: 'Add this to your personal ~/.codex/config.toml. Keep this file private.',
    content: `[mcp_servers.${name}]\nurl = ${JSON.stringify(url)}\nhttp_headers = { Authorization = ${JSON.stringify(`Bearer ${token}`)} }`,
    copyLabel: 'Copy config',
    nextStep: 'Restart Codex to load the server. In the CLI, use /mcp to check the connection.',
  }
  if (client === 'cursor') return {
    instruction: 'Merge this server into your personal ~/.cursor/mcp.json. Keep this file private.',
    content: JSON.stringify({ mcpServers: { [name]: { url, headers: { Authorization: `Bearer ${token}` } } } }, null, 2),
    copyLabel: 'Copy config',
    nextStep: 'Restart Cursor, then check that the server is enabled in MCP settings.',
  }
  return {
    instruction: 'Add a remote MCP server in your client using these values.',
    content: `Name: ${name}\nTransport: Streamable HTTP\nURL: ${url}\nHeader: Authorization\nValue: Bearer ${token}`,
    copyLabel: 'Copy settings',
    nextStep: 'Save the server in your client and refresh its tools to check the connection.',
  }
}
