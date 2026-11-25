import type { Connection } from '../types'

/** Actual token permissions beyond the scopes selected when signing in. */
export function additionalOAuthScopes(connection: Connection): string[] {
  if (connection.auth !== 'oauth' || connection.status === 'revoked') return []
  const requested = new Set(connection.requested_scopes ?? connection.scopes ?? [])
  return [...new Set(connection.scopes ?? [])].filter((scope) => !requested.has(scope)).sort()
}

/** Additional permissions an owner has not accepted yet. These still warn. */
export function unacknowledgedOAuthScopes(connection: Connection): string[] {
  const accepted = new Set(connection.acknowledged_scopes ?? [])
  return additionalOAuthScopes(connection).filter((scope) => !accepted.has(scope))
}
