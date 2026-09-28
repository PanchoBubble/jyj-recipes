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

/** What the calendar asks for, so a chat deep link (which lands on /calendar) renders quietly. */
export const calendarBackdrop = () => [
  http.get(`${API}/meal-slots`, () => HttpResponse.json([])),
  http.get(`${API}/planned-meals`, () => HttpResponse.json([])),
  http.get(`${API}/recipes`, () =>
    HttpResponse.json({ items: [], total: 0, page: 1, page_size: 20 }),
  ),
]
