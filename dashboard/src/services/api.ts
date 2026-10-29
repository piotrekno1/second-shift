export class AuthenticationRequired extends Error {}

/** A refused request. Keeps the status and body for callers that need more than the message. */
export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly payload: unknown = null) {
    super(message)
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`/api${path}`, {
    credentials: 'include',
    headers: { Accept: 'application/json' },
  })
  // 403 means signed in but not allowed, so it stays an ordinary error.
  if (response.status === 401) {
    throw new AuthenticationRequired('Sign in required')
  }
  if (!response.ok) {
    throw await responseError(response)
  }
  return response.json() as Promise<T>
}

export async function apiText(path: string): Promise<string> {
  const response = await fetch(`/api${path}`, {
    credentials: 'include',
    headers: { Accept: 'text/plain' },
  })
  // 403 means signed in but not allowed, so it stays an ordinary error.
  if (response.status === 401) {
    throw new AuthenticationRequired('Sign in required')
  }
  if (!response.ok) {
    throw await responseError(response)
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

interface FieldProblem { loc?: unknown[]; msg?: string }

// Query names that read badly as words.
const FIELD_LABELS: Record<string, string> = { q: 'The search' }

// "String should have at most 200 characters" on body.payload.name reads as
// "Name should have at most 200 characters."
function fieldMessage(problem: FieldProblem): string {
  const field = [...(problem.loc ?? [])].reverse().find((part) => typeof part === 'string' && !['body', 'payload', 'query', 'path'].includes(part))
  const label = typeof field === 'string' ? (FIELD_LABELS[field] ?? field.replace(/_/g, ' ')) : 'This value'
  const text = (problem.msg ?? 'is not valid').replace(/^(Value error, |String |Input |Value )/, '')
  const sentence = `${label.charAt(0).toUpperCase()}${label.slice(1)} ${text.charAt(0).toLowerCase()}${text.slice(1)}`
  return sentence.endsWith('.') ? sentence : `${sentence}.`
}

const STATUS_MESSAGES: Record<number, string> = {
  403: 'You do not have permission to do this.',
  404: 'This was not found. It may have been removed, or the link is wrong.',
  413: 'This is too large to send.',
  429: 'Too many requests. Wait a moment and try again.',
}

async function responseError(response: Response): Promise<ApiError> {
  let payload: { detail?: unknown } | null = null
  try {
    payload = (await response.json()) as { detail?: unknown }
  } catch {
    // The status messages below cover responses without JSON.
  }
  const detail = payload?.detail
  const fail = (message: string): ApiError => new ApiError(message, response.status, payload)
  if (Array.isArray(detail) && detail.length) {
    return fail([...new Set((detail as FieldProblem[]).map(fieldMessage))].join(' '))
  }
  // Django's "Not Found: No Job matches the given query." is less useful than the message below.
  if (typeof detail === 'string' && detail && !detail.startsWith('Not Found')) return fail(detail)
  if (STATUS_MESSAGES[response.status]) return fail(STATUS_MESSAGES[response.status])
  if (response.status >= 500) return fail('The server had a problem. Try again in a moment.')
  return fail(`Request failed with status ${response.status}`)
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
  if (response.status === 204) return undefined as T
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
  // followed here: the caller goes to the sign-in page instead.
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
