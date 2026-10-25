/** Where the server's sign-in and sign-up pages live. */
export const SIGN_IN_PATH = '/accounts/login/'

/** The sign-in page, set to come back to the given path afterwards. */
export function signInUrl(returnTo: string): string {
  // The old dashboard login route has nothing to come back to.
  const next = /^\/login(\?|$)/.test(returnTo) ? '/' : returnTo
  return next === '/' ? SIGN_IN_PATH : `${SIGN_IN_PATH}?next=${encodeURIComponent(next)}`
}
