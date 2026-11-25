import type { Connection } from '../types'

/** Actual token permissions beyond the scopes selected when signing in. */
export function additionalOAuthScopes(connection: Connection): string[] {
  if (connection.auth !== 'oauth' || connection.status === 'revoked') return []
  const requested = new Set(connection.requested_scopes ?? connection.scopes ?? [])
  return [...new Set(connection.scopes ?? [])].filter((scope) => !requested.has(scope)).sort()
}

/** Permissions a token refresh granted beyond what the last sign-in did.
 *  Nobody chose these, so they are the ones worth a look. */
export function grownOAuthScopes(connection: Connection): string[] {
  const signedIn = new Set(connection.signin_scopes ?? connection.scopes ?? [])
  return additionalOAuthScopes(connection).filter((scope) => !signedIn.has(scope))
}
