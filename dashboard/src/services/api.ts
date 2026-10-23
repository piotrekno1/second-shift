export class AuthenticationRequired extends Error {}

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`/api${path}`, {
    credentials: 'include',
    headers: { Accept: 'application/json' },
  })
  if (response.status === 401 || response.status === 403) {
    throw new AuthenticationRequired('Sign in required')
  }
  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`)
  }
  return response.json() as Promise<T>
}

export async function apiText(path: string): Promise<string> {
  const response = await fetch(`/api${path}`, {
    credentials: 'include',
    headers: { Accept: 'text/plain' },
  })
  if (response.status === 401 || response.status === 403) {
    throw new AuthenticationRequired('Sign in required')
  }
  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`)
  }
  return response.text()
}

function csrfToken(): string {
  return (
    document.cookie
      .split('; ')
      .find((entry) => entry.startsWith('csrftoken='))
      ?.split('=')[1] ?? ''
  )
}

async function responseError(response: Response): Promise<Error> {
  try {
    const payload = (await response.json()) as { detail?: string }
    if (payload.detail) return new Error(payload.detail)
  } catch {
    // The status fallback below is sufficient for non-JSON responses.
  }
  return new Error(`Request failed with status ${response.status}`)
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      Accept: 'application/json',
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      'X-CSRFToken': csrfToken(),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) {
    throw await responseError(response)
  }
  return response.json() as Promise<T>
}

export async function apiDelete(path: string): Promise<void> {
  const response = await fetch(`/api${path}`, {
    method: 'DELETE',
    credentials: 'include',
    headers: {
      Accept: 'application/json',
      'X-CSRFToken': csrfToken(),
    },
  })
  if (!response.ok) {
    throw await responseError(response)
  }
}

export async function signOut(): Promise<void> {
  // allauth only logs out on POST. It answers with a redirect, which is not
  // followed here: the caller reloads into the sign-in screen instead.
  const response = await fetch('/accounts/logout/', {
    method: 'POST',
    credentials: 'include',
    redirect: 'manual',
    headers: { 'X-CSRFToken': csrfToken() },
  })
  if (response.type !== 'opaqueredirect' && !response.ok) {
    throw new Error(`Sign out failed with status ${response.status}`)
  }
}
