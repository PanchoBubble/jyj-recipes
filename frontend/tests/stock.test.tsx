import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import type { Ingredient, Unit } from '@/features/ingredients/api'
import type { StockMovementPage } from '@/features/stock/api'

import { renderApp } from './render'
import { API, alice, meAs, problem, server } from './server'

const units: Unit[] = [
  { code: 'g', dimension: 'mass', to_base: '1' },
  { code: 'kg', dimension: 'mass', to_base: '1000' },
  { code: 'ml', dimension: 'volume', to_base: '1' },
  { code: 'l', dimension: 'volume', to_base: '1000' },
  { code: 'cup', dimension: 'volume', to_base: '240' },
  { code: 'piece', dimension: 'count', to_base: '1' },
  { code: 'pinch', dimension: 'none', to_base: null },
]

function ingredient(overrides: Partial<Ingredient> & Pick<Ingredient, 'id' | 'name'>): Ingredient {
  return {
    dimension: 'mass',
    default_unit: 'g',
    category: null,
    grams_per_ml: null,
    grams_per_piece: null,
    stock: { quantity_base: '0.000', base_unit: 'g', display: { amount: '0.000', unit: 'g' } },
    created_at: '2026-09-01T10:00:00Z',
    updated_at: '2026-09-01T10:00:00Z',
    ...overrides,
  }
}

const flour = ingredient({
  id: 1,
  name: 'Flour',
  category: 'Pantry',
  grams_per_ml: '0.530',
  stock: { quantity_base: '1500.000', base_unit: 'g', display: { amount: '1.500', unit: 'kg' } },
})
const eggs = ingredient({
  id: 2,
  name: 'Eggs',
  dimension: 'count',
  default_unit: 'piece',
  category: 'Dairy',
  stock: { quantity_base: '6.000', base_unit: 'piece', display: { amount: '6.000', unit: 'piece' } },
})
const milk = ingredient({
  id: 3,
  name: 'Milk',
  dimension: 'volume',
  default_unit: 'ml',
  category: 'Dairy',
  stock: { quantity_base: '0.000', base_unit: 'ml', display: { amount: '0.000', unit: 'ml' } },
})

const emptyPage: StockMovementPage = { items: [], total: 0, limit: 20, offset: 0 }

function stockOut(i: Ingredient, quantity_base: string, display: { amount: string; unit: string }) {
  return {
    stock: {
      ingredient_id: i.id,
      ingredient_name: i.name,
      dimension: i.dimension,
      quantity_base,
      base_unit: i.stock.base_unit,
      display,
      updated_at: '2026-09-28T12:00:00Z',
    },
    movement: null,
  }
}

function serveIngredients(list: Ingredient[]) {
  server.use(
    http.get(`${API}/ingredients`, () => HttpResponse.json(list)),
    http.get(`${API}/units`, () => HttpResponse.json(units)),
    http.get(`${API}/stock/movements`, () => HttpResponse.json(emptyPage)),
  )
}

function row(name: string) {
  return screen.getByRole('button', { name: new RegExp(`^${name}`) }).closest('li')!
}

beforeEach(() => server.use(meAs(alice)))

describe('stock list', () => {
  it('renders ingredients grouped by category with friendly amounts', async () => {
    serveIngredients([eggs, flour, milk])
    renderApp('/stock')

    expect(await screen.findByText('Flour')).toBeInTheDocument()
    const dairy = screen.getByRole('region', { name: 'Dairy' })
    expect(within(dairy).getByText('Eggs')).toBeInTheDocument()
    expect(within(dairy).getByText('6 pieces')).toBeInTheDocument()
    expect(within(dairy).getByText('Out · 0 ml')).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Pantry' })).getByText('1.5 kg')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove 100 ml Milk' })).toBeDisabled()
  })

  it('filters by search text and by category', async () => {
    serveIngredients([eggs, flour, milk])
    renderApp('/stock')
    const user = userEvent.setup()

    await user.type(await screen.findByLabelText('Search ingredients'), 'fl')
    await waitFor(() => expect(screen.queryByText('Eggs')).not.toBeInTheDocument())
    expect(screen.getByText('Flour')).toBeInTheDocument()

    await user.type(screen.getByLabelText('Search ingredients'), 'x')
    expect(await screen.findByText('No matches')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Clear search' }))
    await user.click(screen.getByRole('radio', { name: 'Dairy' }))
    expect(screen.queryByText('Flour')).not.toBeInTheDocument()
    expect(screen.getByText('Eggs')).toBeInTheDocument()
    expect(screen.getByText('Milk')).toBeInTheDocument()
  })

  it('shows an empty state when there are no ingredients', async () => {
    serveIngredients([])
    renderApp('/stock')
    expect(await screen.findByText('No ingredients yet')).toBeInTheDocument()
  })
})

describe('quick adjust', () => {
  it('sends the delta as a string and updates the amount before the server answers', async () => {
    serveIngredients([flour])
    let body: unknown
    let release!: () => void
    const gate = new Promise<void>((resolve) => (release = resolve))
    server.use(
      http.patch(`${API}/stock/1`, async ({ request }) => {
        body = await request.json()
        await gate
        return HttpResponse.json(stockOut(flour, '1600.000', { amount: '1.600', unit: 'kg' }))
      }),
    )
    renderApp('/stock')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Add 100 g Flour' }))

    expect(within(row('Flour')).getByTestId('stock-quantity')).toHaveTextContent('1.6 kg')
    await waitFor(() => expect(body).toEqual({ delta: '100', unit: 'g' }))
    release()
  })

  it('rolls back and shows a toast when the server rejects the change', async () => {
    serveIngredients([eggs])
    let body: unknown
    let release!: () => void
    const gate = new Promise<void>((resolve) => (release = resolve))
    server.use(
      http.patch(`${API}/stock/2`, async ({ request }) => {
        body = await request.json()
        await gate
        return problem(422, 'Unprocessable Content', 'amount is too large')
      }),
    )
    renderApp('/stock')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Remove 1 piece Eggs' }))
    expect(within(row('Eggs')).getByTestId('stock-quantity')).toHaveTextContent('5 pieces')

    release()
    expect(await screen.findByText("Couldn't update Eggs")).toBeInTheDocument()
    expect(screen.getByText('amount is too large')).toBeInTheDocument()
    expect(within(row('Eggs')).getByTestId('stock-quantity')).toHaveTextContent('6 pieces')
    expect(body).toEqual({ delta: '-1', unit: 'piece' })
  })
})

describe('stock detail', () => {
  it('sets an exact amount in a convertible unit and lists the history', async () => {
    let current = flour
    let body: unknown
    server.use(
      http.get(`${API}/ingredients`, () => HttpResponse.json([current])),
      http.get(`${API}/units`, () => HttpResponse.json(units)),
      http.get(`${API}/stock/movements`, ({ request }) => {
        expect(new URL(request.url).searchParams.get('ingredient_id')).toBe('1')
        return HttpResponse.json({
          ...emptyPage,
          total: 2,
          items: [
            {
              id: 11,
              ingredient_id: 1,
              delta_base: '-300.000',
              shortfall_base: '50.000',
              reason: 'cooked',
              source: 'chat',
              planned_meal_id: 4,
              shopping_list_id: null,
              user_id: 1,
              created_at: new Date().toISOString(),
            },
            {
              id: 10,
              ingredient_id: 1,
              delta_base: '2000.000',
              shortfall_base: '0.000',
              reason: 'purchased',
              source: 'ui',
              planned_meal_id: null,
              shopping_list_id: null,
              user_id: 2,
              created_at: '2026-09-01T10:00:00Z',
            },
          ],
        } satisfies StockMovementPage)
      }),
      http.patch(`${API}/stock/1`, async ({ request }) => {
        body = await request.json()
        current = { ...flour, stock: { quantity_base: '600.000', base_unit: 'g', display: { amount: '600.000', unit: 'g' } } }
        return HttpResponse.json(stockOut(flour, '600.000', { amount: '600.000', unit: 'g' }))
      }),
    )
    renderApp('/stock')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: /^Flour/ }))
    const drawer = await screen.findByRole('dialog')

    const unit = within(drawer).getByRole('combobox', { name: 'Unit' })
    expect(within(unit).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'g',
      'kg',
      'ml',
      'l',
      'cup',
    ])

    expect(await within(drawer).findByText('Cooked')).toBeInTheDocument()
    expect(within(drawer).getByText('-300 g')).toBeInTheDocument()
    expect(within(drawer).getByText('Short by 50 g')).toBeInTheDocument()
    expect(within(drawer).getByText('chat')).toBeInTheDocument()
    expect(within(drawer).getByText(/^You · Today/)).toBeInTheDocument()
    expect(within(drawer).getByText('+2 kg')).toBeInTheDocument()
    expect(within(drawer).getByText(/^User 2 ·/)).toBeInTheDocument()

    const amount = within(drawer).getByLabelText('Set exact amount')
    await user.clear(amount)
    await user.type(amount, '2.5')
    await user.selectOptions(unit, 'cup')
    await user.click(within(drawer).getByRole('button', { name: 'Save amount' }))

    await waitFor(() => expect(body).toEqual({ set_to: '2.5', unit: 'cup' }))
    expect(await within(drawer).findByText('600 g')).toBeInTheDocument()
  })

  it('rejects a malformed exact amount without calling the API', async () => {
    serveIngredients([eggs])
    renderApp('/stock')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: /^Eggs/ }))
    const drawer = await screen.findByRole('dialog')
    const amount = within(drawer).getByLabelText('Set exact amount')
    await user.clear(amount)
    await user.type(amount, '1.2.3')
    await user.click(within(drawer).getByRole('button', { name: 'Save amount' }))

    expect(within(drawer).getByRole('alert')).toHaveTextContent('Enter an amount like 250')
  })
})

describe('ingredient management', () => {
  it('validates the form and creates an ingredient', async () => {
    serveIngredients([flour])
    let body: unknown
    server.use(
      http.post(`${API}/ingredients`, async ({ request }) => {
        body = await request.json()
        return HttpResponse.json(ingredient({ id: 9, name: 'Butter' }), { status: 201 })
      }),
    )
    renderApp('/stock')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Add ingredient' }))
    const drawer = await screen.findByRole('dialog')
    await user.type(within(drawer).getByLabelText('Grams per ml'), '0')
    await user.click(within(drawer).getByRole('button', { name: 'Add ingredient' }))

    expect(await within(drawer).findByText('Enter a name')).toBeInTheDocument()
    expect(within(drawer).getByText('Must be more than 0')).toBeInTheDocument()
    expect(body).toBeUndefined()

    await user.type(within(drawer).getByLabelText('Name'), '  Butter ')
    await user.selectOptions(within(drawer).getByLabelText('Measured by'), 'count')
    expect(within(drawer).getByLabelText('Default unit')).toHaveValue('piece')
    await user.selectOptions(within(drawer).getByLabelText('Measured by'), 'mass')
    await user.selectOptions(within(drawer).getByLabelText('Default unit'), 'kg')
    await user.type(within(drawer).getByLabelText('Category'), 'Dairy')
    await user.clear(within(drawer).getByLabelText('Grams per ml'))
    await user.type(within(drawer).getByLabelText('Grams per ml'), '0,91')
    await user.click(within(drawer).getByRole('button', { name: 'Add ingredient' }))

    await waitFor(() =>
      expect(body).toEqual({
        name: 'Butter',
        dimension: 'mass',
        default_unit: 'kg',
        category: 'Dairy',
        grams_per_ml: '0.91',
        grams_per_piece: null,
      }),
    )
    expect(await screen.findByText('Added Butter')).toBeInTheDocument()
  })

  it('shows the 409 problem detail when deleting an ingredient in use', async () => {
    serveIngredients([flour])
    server.use(
      http.delete(`${API}/ingredients/1`, () =>
        HttpResponse.json(
          {
            type: 'about:blank',
            title: 'Conflict',
            status: 409,
            detail: 'ingredient is in use and cannot be deleted',
            references: ['stock_movements'],
          },
          { status: 409, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    )
    renderApp('/stock')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: /^Flour/ }))
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Edit' }))
    const form = await screen.findByRole('dialog', { name: 'Edit ingredient' })
    expect(within(form).getByLabelText('Name')).toHaveValue('Flour')
    expect(within(form).getByLabelText('Grams per ml')).toHaveValue('0.53')

    await user.click(within(form).getByRole('button', { name: 'Delete ingredient' }))
    await user.click(within(form).getByRole('button', { name: 'Delete' }))

    expect(await within(form).findByRole('alert')).toHaveTextContent(
      'ingredient is in use and cannot be deleted (used by stock history)',
    )
    expect(screen.getByRole('dialog', { name: 'Edit ingredient' })).toBeInTheDocument()
  })
  it('lists the recipes that block a conversion change and links to their editors', async () => {
    serveIngredients([flour])
    server.use(
      http.patch(`${API}/ingredients/1`, () =>
        HttpResponse.json(
          {
            type: 'about:blank',
            title: 'Conflict',
            status: 409,
            detail: "recipes use 'Flour' in units that would no longer convert; change those lines first",
            recipes: [
              { id: 7, name: 'Pancakes', units: ['cup'] },
              { id: 8, name: 'Bread', units: ['cup', 'ml'] },
            ],
          },
          { status: 409, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
      http.get(`${API}/recipes/7`, () => problem(404, 'Not Found')),
    )
    const { router } = renderApp('/stock')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: /^Flour/ }))
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Edit' }))
    const form = await screen.findByRole('dialog', { name: 'Edit ingredient' })
    await user.clear(within(form).getByLabelText('Grams per ml'))
    await user.click(within(form).getByRole('button', { name: 'Save' }))

    const alert = await within(form).findByRole('alert')
    expect(alert).toHaveTextContent('Used by these recipes in units that need this conversion')
    expect(alert).not.toHaveTextContent('would no longer convert')
    expect(within(alert).getByRole('link', { name: 'Pancakes' })).toHaveAttribute('href', '/recipes/7/edit')
    expect(within(alert).getByRole('link', { name: 'Bread' })).toHaveAttribute('href', '/recipes/8/edit')
    expect(alert).toHaveTextContent('(cup, ml)')

    await user.click(within(alert).getByRole('link', { name: 'Pancakes' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/recipes/7/edit'))
  })
})
