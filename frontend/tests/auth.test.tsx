import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { renderApp } from './render'
import { API, alice, meAs, problem, server } from './server'

describe('login', () => {
  it('signs in and returns to the requested page', async () => {
    let body: unknown
    let csrf: string | null = null
    server.use(
      http.post(`${API}/auth/login`, async ({ request }) => {
        body = await request.json()
        csrf = request.headers.get('X-Requested-With')
        server.use(meAs(alice))
        return HttpResponse.json(alice)
      }),
    )
    const { router } = renderApp('/recipes')

    await screen.findByRole('heading', { name: 'jyj recipes' })
    expect(router.state.location.pathname).toBe('/login')
    expect(router.state.location.search).toBe('?next=%2Frecipes')

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Username'), 'alice')
    await user.type(screen.getByLabelText('Password'), 'correct horse')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Recipes' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/recipes')
    expect(body).toEqual({ username: 'alice', password: 'correct horse' })
    expect(csrf).toBe('jyj')
  })

  it('shows the problem detail when credentials are wrong', async () => {
    server.use(
      http.post(`${API}/auth/login`, () =>
        problem(401, 'Unauthorized', 'Invalid username or password'),
      ),
    )
    const { router } = renderApp('/login')

    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Username'), 'alice')
    await user.type(screen.getByLabelText('Password'), 'wrong')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid username or password')
    expect(screen.getByLabelText('Password')).toHaveValue('')
    expect(router.state.location.pathname).toBe('/login')
  })

  it('validates empty fields without calling the API', async () => {
    renderApp('/login')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('Enter your username')).toBeInTheDocument()
    expect(screen.getByText('Enter your password')).toBeInTheDocument()
  })
})

describe('route guard', () => {
  it('redirects to login when me is 401', async () => {
    const { router } = renderApp('/pantry')
    await screen.findByLabelText('Username')
    expect(router.state.location.pathname).toBe('/login')
    expect(router.state.location.search).toBe('?next=%2Fpantry')
  })

  it('redirects to login when any request returns 401 mid-session', async () => {
    server.use(meAs(alice))
    server.use(http.get(`${API}/meal-slots`, () => HttpResponse.json([])))
    const { router, queryClient } = renderApp('/settings')
    await screen.findByRole('heading', { level: 1, name: 'Settings' })

    server.use(
      meAs(null),
      http.get(`${API}/anything`, () => problem(401, 'Unauthorized', 'Not authenticated')),
    )
    await queryClient
      .fetchQuery({
        queryKey: ['anything'],
        queryFn: () => import('@/lib/api').then(({ api }) => api.get('/anything')),
      })
      .catch(() => undefined)

    await screen.findByLabelText('Username')
    expect(router.state.location.pathname).toBe('/login')
    expect(router.state.location.search).toBe('?next=%2Fsettings')
  })

  it('sends a signed-in user away from /login', async () => {
    server.use(meAs(alice))
    const { router } = renderApp('/login')
    await screen.findByRole('heading', { level: 1, name: 'Calendar' })
    expect(router.state.location.pathname).toBe('/calendar')
  })
})

describe('logout', () => {
  it('posts with the CSRF header and returns to login', async () => {
    const headers: (string | null)[] = []
    server.use(
      meAs(alice),
      http.get(`${API}/meal-slots`, () => HttpResponse.json([])),
      http.post(`${API}/auth/logout`, ({ request }) => {
        headers.push(request.headers.get('X-Requested-With'))
        server.use(meAs(null))
        return new HttpResponse(null, { status: 204 })
      }),
    )
    const { router } = renderApp('/settings')

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Log out' }))

    await screen.findByLabelText('Username')
    expect(headers).toEqual(['jyj'])
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(router.state.location.search).toBe('')
  })
})
