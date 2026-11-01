import type { Connection } from '../types'

/** Actual token permissions beyond the scopes selected when signing in. */
export function additionalOAuthScopes(connection: Connection): string[] {
  if (connection.auth !== 'oauth' || connection.status === 'revoked') return []
  const requested = new Set(connection.requested_scopes ?? connection.scopes ?? [])
  return [...new Set(connection.scopes ?? [])].filter((scope) => !requested.has(scope)).sort()
}
