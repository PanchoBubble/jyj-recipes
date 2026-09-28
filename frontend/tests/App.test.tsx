import { render, screen } from '@testing-library/react'

import App from '@/App'

describe('App', () => {
  it('renders the heading and a shadcn button', () => {
    render(<App />)

    expect(screen.getByRole('heading', { name: 'jyj recipes' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Get started' })).toHaveAttribute(
      'data-slot',
      'button',
    )
  })
})
