import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { setThemePreference } from '@/lib/theme'

import { renderApp } from './render'
import { alice, meAs, server } from './server'

beforeEach(() => server.use(meAs(alice)))

describe('app layout', () => {
  it('defaults to the calendar and renders all tabs', async () => {
    const { router } = renderApp('/')
    await screen.findByRole('heading', { level: 1, name: 'Calendar' })
    expect(router.state.location.pathname).toBe('/calendar')

    const nav = screen.getByRole('navigation', { name: 'Main' })
    const links = within(nav).getAllByRole('link')
    expect(links.map((l) => l.textContent)).toEqual([
      'Calendar',
      'Recipes',
      'Shopping',
      'Pantry',
    ])
    expect(within(nav).getByRole('link', { name: 'Calendar' })).toHaveAttribute(
      'aria-current',
      'page',
    )
  })

  it('navigates between tabs and to settings', async () => {
    const { router } = renderApp('/calendar')
    const user = userEvent.setup()
    const nav = await screen.findByRole('navigation', { name: 'Main' })

    for (const name of ['Recipes', 'Shopping', 'Pantry']) {
      await user.click(within(nav).getByRole('link', { name }))
      expect(await screen.findByRole('heading', { level: 1, name })).toBeInTheDocument()
      expect(router.state.location.pathname).toBe(`/${name.toLowerCase()}`)
      expect(within(nav).getByRole('link', { name })).toHaveAttribute('aria-current', 'page')
    }

    await user.click(screen.getByRole('link', { name: 'Settings' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Settings' })).toBeInTheDocument()
    expect(await screen.findByText('Alice')).toBeInTheDocument()
  })

  it('persists the theme choice and toggles the dark class', async () => {
    renderApp('/settings')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('radio', { name: 'Dark' }))
    expect(document.documentElement).toHaveClass('dark')
    expect(localStorage.getItem('jyj-theme')).toBe('dark')
    expect(screen.getByRole('radio', { name: 'Dark' })).toHaveAttribute('aria-checked', 'true')

    await user.click(screen.getByRole('radio', { name: 'Light' }))
    expect(document.documentElement).not.toHaveClass('dark')
    expect(localStorage.getItem('jyj-theme')).toBe('light')

    setThemePreference('system')
  })
})
