import { render } from '@testing-library/react'
import { createMemoryRouter } from 'react-router'

import App from '@/App'
import { createQueryClient } from '@/lib/query'
import { routes } from '@/routes'

export function renderApp(path = '/') {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  const queryClient = createQueryClient()
  const view = render(<App router={router} queryClient={queryClient} />)
  return { ...view, router, queryClient }
}
