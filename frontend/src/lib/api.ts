export const API_BASE = '/api/v1'

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

export interface Problem {
  type: string
  title: string
  status: number
  detail?: string
  [extension: string]: unknown
}

export class ApiError extends Error {
  readonly status: number
  readonly type: string
  readonly title: string
  readonly detail?: string
  /** Extra RFC 9457 members, e.g. `references` on a 409 or `errors` on a 422. */
  readonly extensions: Record<string, unknown>

  constructor(problem: Problem) {
    const { type, title, status, detail, ...extensions } = problem
    super(detail ?? title)
    this.name = 'ApiError'
    this.status = status
    this.type = type
    this.title = title
    this.detail = detail
    this.extensions = extensions
  }

  get isClientError() {
    return this.status >= 400 && this.status < 500
  }
}

export function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401
}

export interface RequestOptions {
  method?: string
  body?: unknown
  signal?: AbortSignal
}

async function toApiError(response: Response): Promise<ApiError> {
  const fallback: Problem = {
    type: 'about:blank',
    title: response.statusText || 'Request failed',
    status: response.status,
  }
  const contentType = response.headers.get('content-type') ?? ''
  if (!contentType.includes('json')) return new ApiError(fallback)
  try {
    const data = (await response.json()) as Partial<Problem>
    return new ApiError({
      ...data,
      type: typeof data.type === 'string' ? data.type : fallback.type,
      title: typeof data.title === 'string' ? data.title : fallback.title,
      status: typeof data.status === 'number' ? data.status : fallback.status,
      detail: typeof data.detail === 'string' ? data.detail : undefined,
    })
  } catch {
    return new ApiError(fallback)
  }
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = (options.method ?? 'GET').toUpperCase()
  const headers = new Headers({ Accept: 'application/json, application/problem+json' })
  if (!SAFE_METHODS.has(method)) headers.set('X-Requested-With', 'jyj')

  let body: BodyInit | undefined
  if (options.body !== undefined) {
    headers.set('Content-Type', 'application/json')
    body = JSON.stringify(options.body)
  }

  // Absolute URL so the same code works under jsdom, where fetch has no base URL.
  const url = new URL(`${API_BASE}${path}`, window.location.origin)

  let response: Response
  try {
    response = await fetch(url, {
      method,
      headers,
      body,
      credentials: 'same-origin',
      signal: options.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError({ type: 'about:blank', title: 'Network error', status: 0 })
  }

  if (!response.ok) throw await toApiError(response)
  if (response.status === 204) return undefined as T
  const text = await response.text()
  return (text ? JSON.parse(text) : undefined) as T
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => apiFetch<T>(path, { signal }),
  post: <T>(path: string, body?: unknown) => apiFetch<T>(path, { method: 'POST', body }),
  patch: <T>(path: string, body?: unknown) => apiFetch<T>(path, { method: 'PATCH', body }),
  put: <T>(path: string, body?: unknown) => apiFetch<T>(path, { method: 'PUT', body }),
  delete: <T>(path: string) => apiFetch<T>(path, { method: 'DELETE' }),
}
