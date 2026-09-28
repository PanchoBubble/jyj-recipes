import { http, HttpResponse } from 'msw'

import { ApiError, api } from '@/lib/api'

import { API, problem, server } from './server'

describe('api client', () => {
  it('sends X-Requested-With only on mutations, with same-origin credentials', async () => {
    const seen: Record<string, string | null> = {}
    server.use(
      http.all(`${API}/probe`, ({ request }) => {
        seen[request.method] = request.headers.get('X-Requested-With')
        return HttpResponse.json({ ok: true })
      }),
    )

    await api.get('/probe')
    await api.post('/probe', { a: 1 })
    await api.patch('/probe', { a: 1 })
    await api.delete('/probe')

    expect(seen).toEqual({ GET: null, POST: 'jyj', PATCH: 'jyj', DELETE: 'jyj' })
  })

  it('parses problem+json into ApiError', async () => {
    server.use(http.get(`${API}/probe`, () => problem(409, 'Conflict', 'Ingredient in use')))

    const error = await api.get('/probe').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      status: 409,
      title: 'Conflict',
      detail: 'Ingredient in use',
      message: 'Ingredient in use',
    })
  })

  it('falls back to the status text for non-JSON errors', async () => {
    server.use(
      http.get(`${API}/probe`, () => new HttpResponse('oops', { status: 502, statusText: 'Bad Gateway' })),
    )
    await expect(api.get('/probe')).rejects.toMatchObject({ status: 502, title: 'Bad Gateway' })
  })
})
