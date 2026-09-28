import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'

export const API = '/api/v1'

export const alice = { id: 1, username: 'alice', display_name: 'Alice' }

export function problem(status: number, title: string, detail?: string) {
  return HttpResponse.json(
    { type: 'about:blank', title, status, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  )
}

export const meAs = (user: typeof alice | null) =>
  http.get(`${API}/auth/me`, () =>
    user ? HttpResponse.json(user) : problem(401, 'Unauthorized', 'Not authenticated'),
  )

export const server = setupServer(meAs(null))
