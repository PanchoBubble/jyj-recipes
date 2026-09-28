import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import type { Ingredient, Unit } from '@/features/ingredients/api'
import type { Recipe, RecipeInput, RecipeSummary, ScaledRecipe } from '@/features/recipes/api'
import { mapRecipeProblem } from '@/features/recipes/form'
import { ApiError } from '@/lib/api'

import { renderApp } from './render'
import { API, alice, meAs, server } from './server'

const units: Unit[] = [
  { code: 'g', dimension: 'mass', to_base: '1' },
  { code: 'kg', dimension: 'mass', to_base: '1000' },
  { code: 'ml', dimension: 'volume', to_base: '1' },
  { code: 'l', dimension: 'volume', to_base: '1000' },
  { code: 'cup', dimension: 'volume', to_base: '240' },
  { code: 'piece', dimension: 'count', to_base: '1' },
  { code: 'pinch', dimension: 'none', to_base: null },
  { code: 'to_taste', dimension: 'none', to_base: null },
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

const flour = ingredient({ id: 1, name: 'Flour', grams_per_ml: '0.530' })
const eggs = ingredient({ id: 2, name: 'Eggs', dimension: 'count', default_unit: 'piece' })
const salt = ingredient({ id: 3, name: 'Salt' })

function summary(overrides: Partial<RecipeSummary> & Pick<RecipeSummary, 'id' | 'name'>): RecipeSummary {
  return {
    description: null,
    default_servings: 2,
    ingredient_count: 0,
    photo_url: null,
    photo_thumb_url: null,
    created_by: 1,
    created_at: '2026-09-01T10:00:00Z',
    updated_at: '2026-09-01T10:00:00Z',
    archived_at: null,
    ...overrides,
  }
}

const pancakes: Recipe = {
  ...summary({
    id: 7,
    name: 'Pancakes',
    description: 'Sunday breakfast',
    default_servings: 2,
    ingredient_count: 3,
  }),
  ingredients: [
    {
      id: 71,
      position: 0,
      ingredient_id: 1,
      ingredient_name: 'Flour',
      dimension: 'mass',
      amount_per_person: '62.500',
      unit: 'g',
      unit_dimension: 'mass',
      note: 'sifted',
    },
    {
      id: 72,
      position: 1,
      ingredient_id: 2,
      ingredient_name: 'Eggs',
      dimension: 'count',
      amount_per_person: '1.000',
      unit: 'piece',
      unit_dimension: 'count',
      note: null,
    },
    {
      id: 73,
      position: 2,
      ingredient_id: 3,
      ingredient_name: 'Salt',
      dimension: 'mass',
      amount_per_person: null,
      unit: 'pinch',
      unit_dimension: 'none',
      note: null,
    },
  ],
}

function scaledPancakes(servings: number): ScaledRecipe {
  const flourG = 62.5 * servings
  return {
    recipe_id: 7,
    name: 'Pancakes',
    servings,
    ingredients: [
      {
        position: 0,
        ingredient_id: 1,
        ingredient_name: 'Flour',
        amount_per_person: '62.500',
        unit: 'g',
        amount: flourG.toFixed(3),
        display: flourG >= 1000 ? { amount: (flourG / 1000).toFixed(3), unit: 'kg' } : { amount: flourG.toFixed(3), unit: 'g' },
        note: 'sifted',
      },
      {
        position: 1,
        ingredient_id: 2,
        ingredient_name: 'Eggs',
        amount_per_person: '1.000',
        unit: 'piece',
        amount: `${servings}.000`,
        display: { amount: `${servings}.000`, unit: 'piece' },
        note: null,
      },
      {
        position: 2,
        ingredient_id: 3,
        ingredient_name: 'Salt',
        amount_per_person: null,
        unit: 'pinch',
        amount: null,
        display: null,
        note: null,
      },
    ],
  }
}

function problem422(body: Record<string, unknown>) {
  return HttpResponse.json(
    { type: 'about:blank', title: 'Unprocessable Content', status: 422, ...body },
    { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
  )
}

function serveCatalog() {
  server.use(
    http.get(`${API}/units`, () => HttpResponse.json(units)),
    http.get(`${API}/ingredients`, () => HttpResponse.json([flour, eggs, salt])),
  )
}

function serveRecipe(recipe: Recipe) {
  server.use(
    http.get(`${API}/recipes/${recipe.id}`, () => HttpResponse.json(recipe)),
    http.get(`${API}/recipes/${recipe.id}/scaled`, ({ request }) => {
      const servings = Number(new URL(request.url).searchParams.get('servings'))
      return HttpResponse.json(scaledPancakes(servings))
    }),
  )
}

const SEARCH = 'Search or add an ingredient'

function line(n: number) {
  return screen.getByRole('listitem', { name: `Ingredient ${n}` })
}

beforeEach(() => server.use(meAs(alice)))

describe('recipe list', () => {
  const all = Array.from({ length: 25 }, (_, i) =>
    summary({
      id: i + 1,
      name: i === 0 ? 'Pancakes' : `Recipe ${String(i + 1).padStart(2, '0')}`,
      description: i === 0 ? 'Sunday breakfast' : null,
      ingredient_count: i === 0 ? 3 : 1,
      photo_thumb_url: i === 1 ? '/photos/2-thumb.webp' : null,
    }),
  )
  const queries: (string | null)[] = []

  beforeEach(() => {
    queries.length = 0
    server.use(
      http.get(`${API}/recipes`, ({ request }) => {
        const params = new URL(request.url).searchParams
        const q = params.get('q')
        queries.push(q)
        const page = Number(params.get('page') ?? 1)
        const size = Number(params.get('page_size') ?? 20)
        const matches = all.filter((r) => !q || r.name.toLowerCase().includes(q.toLowerCase()))
        return HttpResponse.json({
          items: matches.slice((page - 1) * size, page * size),
          total: matches.length,
          page,
          page_size: size,
        })
      }),
    )
  })

  it('shows cards, loads more pages and searches on the server', async () => {
    renderApp('/recipes')
    const user = userEvent.setup()

    const card = (await screen.findByText('Pancakes')).closest('a')!
    expect(card).toHaveAttribute('href', '/recipes/1')
    expect(within(card).getByText('Sunday breakfast')).toBeInTheDocument()
    expect(within(card).getByText('3 ingredients')).toBeInTheDocument()
    expect(within(card).getByRole('img', { name: 'Pancakes (no photo)' })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Recipe 02' })).toHaveAttribute('src', '/photos/2-thumb.webp')
    expect(screen.getByRole('link', { name: 'New recipe' })).toHaveAttribute('href', '/recipes/new')

    expect(screen.queryByText('Recipe 25')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Load more (20 of 25)' }))
    expect(await screen.findByText('Recipe 25')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Load more/ })).not.toBeInTheDocument()

    await user.type(screen.getByLabelText('Search recipes'), 'pan')
    await waitFor(() => expect(screen.queryByText('Recipe 02')).not.toBeInTheDocument())
    expect(screen.getByText('Pancakes')).toBeInTheDocument()
    expect(queries).toContain('pan')
    expect(queries).not.toContain('p')

    await user.type(screen.getByLabelText('Search recipes'), 'zz')
    expect(await screen.findByText('No matches')).toBeInTheDocument()
  })

  it('shows an empty state with a create action', async () => {
    server.use(
      http.get(`${API}/recipes`, () =>
        HttpResponse.json({ items: [], total: 0, page: 1, page_size: 20 }),
      ),
    )
    renderApp('/recipes')
    expect(await screen.findByText('No recipes yet')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: /New recipe/ })).toHaveLength(2)
  })
})

describe('recipe detail', () => {
  it('scales amounts through the API when servings change', async () => {
    serveRecipe(pancakes)
    const requested: number[] = []
    server.events.on('request:start', ({ request }) => {
      const url = new URL(request.url)
      if (url.pathname.endsWith('/scaled')) requested.push(Number(url.searchParams.get('servings')))
    })
    renderApp('/recipes/7')
    const user = userEvent.setup()

    expect(await screen.findByRole('heading', { name: 'Pancakes' })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Pancakes (no photo)' })).toBeInTheDocument()
    const amounts = () => screen.getAllByTestId('scaled-amount').map((el) => el.textContent)
    await waitFor(() => expect(amounts()).toEqual(['125 g', '2 pieces', 'pinch']))
    expect(screen.getByRole('group', { name: 'Servings' })).toHaveTextContent('2')

    const more = screen.getByRole('button', { name: 'More servings' })
    for (let i = 0; i < 14; i++) await user.click(more)
    expect(screen.getByRole('group', { name: 'Servings' })).toHaveTextContent('16')
    await waitFor(() => expect(amounts()).toEqual(['1 kg', '16 pieces', 'pinch']))
    // Debounced: rapid taps don't request every intermediate value.
    expect(requested).toContain(16)
    expect(requested.length).toBeLessThan(6)
    server.events.removeAllListeners()
  })

  it('deletes after confirming', async () => {
    serveRecipe(pancakes)
    let deleted = false
    server.use(
      http.delete(`${API}/recipes/7`, () => {
        deleted = true
        return new HttpResponse(null, { status: 204 })
      }),
      http.get(`${API}/recipes`, () =>
        HttpResponse.json({ items: [], total: 0, page: 1, page_size: 20 }),
      ),
    )
    renderApp('/recipes/7')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Delete recipe' }))
    const dialog = screen.getByRole('alertdialog', { name: 'Delete Pancakes?' })
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(deleted).toBe(false)

    await user.click(screen.getByRole('button', { name: 'Delete recipe' }))
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Delete' }))
    expect(await screen.findByText('Deleted Pancakes')).toBeInTheDocument()
    expect(deleted).toBe(true)
    expect(await screen.findByText('No recipes yet')).toBeInTheDocument()
  })

  it('explains when the API archived instead of deleting', async () => {
    serveRecipe(pancakes)
    server.use(
      http.delete(`${API}/recipes/7`, () =>
        HttpResponse.json({ ...pancakes, archived_at: '2026-09-28T10:00:00Z' }),
      ),
      http.get(`${API}/recipes`, () =>
        HttpResponse.json({ items: [], total: 0, page: 1, page_size: 20 }),
      ),
    )
    renderApp('/recipes/7')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Delete recipe' }))
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Delete' }))
    expect(await screen.findByText('Archived Pancakes')).toBeInTheDocument()
    expect(screen.getByText(/archived instead of deleted/)).toBeInTheDocument()
  })
})

describe('recipe editor', () => {
  it('creates a recipe with an existing and a new ingredient', async () => {
    serveCatalog()
    let body: RecipeInput | null = null
    let ingredientFetches = 0
    server.use(
      http.get(`${API}/ingredients`, () => {
        ingredientFetches++
        return HttpResponse.json([flour, eggs, salt])
      }),
      http.post(`${API}/recipes`, async ({ request }) => {
        body = (await request.json()) as RecipeInput
        return HttpResponse.json({ ...pancakes, id: 9, name: 'Pesto' }, { status: 201 })
      }),
      http.get(`${API}/recipes/9`, () => HttpResponse.json({ ...pancakes, id: 9, name: 'Pesto' })),
      http.get(`${API}/recipes/9/scaled`, () => HttpResponse.json(scaledPancakes(2))),
    )
    renderApp('/recipes/new')
    const user = userEvent.setup()

    await user.type(await screen.findByLabelText('Name'), 'Pesto')
    await user.type(screen.getByLabelText('Description'), 'Green')
    await user.click(screen.getByRole('button', { name: 'More servings' }))

    const first = line(1)
    await user.type(within(first).getByPlaceholderText(SEARCH), 'fl')
    await user.click(await screen.findByRole('option', { name: /Flour/ }))
    expect(within(first).getByPlaceholderText(SEARCH)).toHaveValue('Flour')
    const unit = within(first).getByLabelText('Unit')
    expect(unit).toHaveValue('g')
    // Flour has grams per ml, so volume units convert; count units don't.
    expect(within(unit).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'g', 'kg', 'ml', 'l', 'cup', 'pinch', 'to taste',
    ])
    await user.selectOptions(unit, 'kg')
    await user.type(within(first).getByLabelText('Per person'), '0,25')
    await user.type(within(first).getByLabelText('Note'), 'sifted')

    await user.click(screen.getByRole('button', { name: 'Add ingredient' }))
    const second = line(2)
    await user.type(within(second).getByPlaceholderText(SEARCH), 'Basil')
    await user.click(await screen.findByRole('option', { name: 'Create “Basil”' }))
    expect(within(second).getByText('New ingredient')).toBeInTheDocument()
    await user.selectOptions(within(second).getByLabelText('Measured by'), 'count')
    expect(within(second).getByLabelText('Unit')).toHaveValue('piece')
    await user.type(within(second).getByLabelText('Per person'), '4')

    const before = ingredientFetches
    await user.click(screen.getByRole('button', { name: 'Create recipe' }))

    expect(await screen.findByText('Added Pesto')).toBeInTheDocument()
    expect(body).toEqual({
      name: 'Pesto',
      description: 'Green',
      default_servings: 3,
      ingredients: [
        { ingredient_id: 1, amount_per_person: '0.25', unit: 'kg', note: 'sifted' },
        {
          new_ingredient: { name: 'Basil', dimension: 'count', default_unit: 'piece' },
          amount_per_person: '4',
          unit: 'piece',
          note: null,
        },
      ],
    })
    await waitFor(() => expect(ingredientFetches).toBeGreaterThan(before))
    expect(await screen.findByRole('heading', { name: 'Pesto' })).toBeInTheDocument()
  })

  it('validates rows before sending', async () => {
    serveCatalog()
    let posted = false
    server.use(
      http.post(`${API}/recipes`, () => {
        posted = true
        return HttpResponse.json({}, { status: 500 })
      }),
    )
    renderApp('/recipes/new')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Create recipe' }))
    expect(await screen.findByText('Enter a name')).toBeInTheDocument()
    expect(within(line(1)).getByText('Pick an ingredient')).toBeInTheDocument()
    expect(within(line(1)).getByText('Enter an amount')).toBeInTheDocument()

    await user.type(within(line(1)).getByPlaceholderText(SEARCH), 'Salt')
    await user.click(await screen.findByRole('option', { name: /Salt/ }))
    await user.selectOptions(within(line(1)).getByLabelText('Unit'), 'to_taste')
    await waitFor(() =>
      expect(within(line(1)).queryByText('Enter an amount')).not.toBeInTheDocument(),
    )
    expect(posted).toBe(false)
  })

  it('reorders and removes rows, sending the new order on save', async () => {
    serveCatalog()
    serveRecipe(pancakes)
    let body: RecipeInput | null = null
    server.use(
      http.patch(`${API}/recipes/7`, async ({ request }) => {
        body = (await request.json()) as RecipeInput
        return HttpResponse.json(pancakes)
      }),
    )
    renderApp('/recipes/7/edit')
    const user = userEvent.setup()

    expect(await screen.findByDisplayValue('Pancakes')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Move Flour up' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Move Salt down' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Move Salt up' }))
    await user.click(screen.getByRole('button', { name: 'Move Salt up' }))
    expect(within(line(1)).getByPlaceholderText(SEARCH)).toHaveValue('Salt')
    expect(within(line(2)).getByPlaceholderText(SEARCH)).toHaveValue('Flour')
    expect(within(line(2)).getByLabelText('Note')).toHaveValue('sifted')

    await user.click(screen.getByRole('button', { name: 'Move Flour down' }))
    await user.click(screen.getByRole('button', { name: 'Remove Eggs' }))
    await user.click(screen.getByRole('button', { name: 'Save recipe' }))

    expect(await screen.findByText('Saved Pancakes')).toBeInTheDocument()
    expect(body!.ingredients).toEqual([
      { ingredient_id: 3, amount_per_person: null, unit: 'pinch', note: null },
      { ingredient_id: 1, amount_per_person: '62.5', unit: 'g', note: 'sifted' },
    ])
  })

  it('shows API validation problems on the offending row', async () => {
    serveCatalog()
    serveRecipe(pancakes)
    server.use(
      http.patch(`${API}/recipes/7`, () =>
        problem422({
          detail: "ingredients[1]: cannot use 'cup' for 'Eggs' (count)",
          row: 1,
          reason: 'missing_factor',
        }),
      ),
    )
    renderApp('/recipes/7/edit')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Save recipe' }))
    expect(
      await within(line(2)).findByText("cannot use 'cup' for 'Eggs' (count)"),
    ).toBeInTheDocument()
    expect(within(line(2)).getByLabelText('Unit')).toHaveAttribute('aria-invalid', 'true')
    expect(within(line(1)).queryByText(/cannot use/)).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Fix the highlighted fields')

    server.use(
      http.patch(`${API}/recipes/7`, () =>
        problem422({
          detail: 'Request validation failed',
          errors: [
            {
              loc: ['body', 'ingredients', 0, 'amount_per_person'],
              msg: 'Input should be greater than or equal to 0',
              type: 'greater_than_equal',
            },
          ],
        }),
      ),
    )
    await user.click(screen.getByRole('button', { name: 'Save recipe' }))
    expect(
      await within(line(1)).findByText('Input should be greater than or equal to 0'),
    ).toBeInTheDocument()
    expect(within(line(1)).getByLabelText('Per person')).toHaveAttribute('aria-invalid', 'true')
  })
})

describe('recipe editor unsaved changes', () => {
  function beforeUnloadPrevented() {
    const event = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(event)
    return event.defaultPrevented
  }

  it('leaves without asking when nothing changed', async () => {
    serveCatalog()
    serveRecipe(pancakes)
    const { router } = renderApp('/recipes/7/edit')
    const user = userEvent.setup()

    expect(await screen.findByDisplayValue('Pancakes')).toBeInTheDocument()
    expect(beforeUnloadPrevented()).toBe(false)
    await user.click(screen.getByRole('link', { name: 'Back to recipe' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/recipes/7'))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })

  it('asks before discarding edits, and keeps them when the user stays', async () => {
    serveCatalog()
    serveRecipe(pancakes)
    const { router } = renderApp('/recipes/7/edit')
    const user = userEvent.setup()

    const name = await screen.findByDisplayValue('Pancakes')
    await user.type(name, ' deluxe')
    expect(beforeUnloadPrevented()).toBe(true)

    await user.click(screen.getByRole('link', { name: 'Back to recipe' }))
    const dialog = await screen.findByRole('alertdialog', { name: 'Discard unsaved changes?' })
    expect(router.state.location.pathname).toBe('/recipes/7/edit')

    await user.click(within(dialog).getByRole('button', { name: 'Keep editing' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(router.state.location.pathname).toBe('/recipes/7/edit')
    expect(screen.getByLabelText('Name')).toHaveValue('Pancakes deluxe')

    await user.click(screen.getByRole('link', { name: 'Back to recipe' }))
    await user.click(
      within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Discard' }),
    )
    await waitFor(() => expect(router.state.location.pathname).toBe('/recipes/7'))
    expect(beforeUnloadPrevented()).toBe(false)
  })

  it('guards a new recipe with only a pending photo', async () => {
    serveCatalog()
    const { router } = renderApp('/recipes/new')
    const user = userEvent.setup()

    await screen.findByRole('button', { name: 'Create recipe' })
    const file = new File(['x'], 'pancakes.jpg', { type: 'image/jpeg' })
    await user.upload(screen.getByLabelText('Photo file from library'), file)
    await user.click(screen.getByRole('link', { name: /pantry/i }))

    expect(await screen.findByRole('alertdialog')).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/recipes/new')
  })

  it('saving clears the guard', async () => {
    serveCatalog()
    serveRecipe(pancakes)
    server.use(
      http.patch(`${API}/recipes/7`, () => HttpResponse.json({ ...pancakes, name: 'Pancakes deluxe' })),
    )
    const { router } = renderApp('/recipes/7/edit')
    const user = userEvent.setup()

    await user.type(await screen.findByDisplayValue('Pancakes'), ' deluxe')
    await user.click(screen.getByRole('button', { name: 'Save recipe' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/recipes/7'))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(beforeUnloadPrevented()).toBe(false)
  })
})

describe('mapRecipeProblem', () => {
  const err = (extra: Record<string, unknown>) =>
    new ApiError({ type: 'about:blank', title: 'Conflict', status: 409, ...extra })

  it('puts conflicts for inline new ingredients on the ingredient field', () => {
    expect(
      mapRecipeProblem(err({ detail: "ingredients[3]: ingredient 'Basil' already exists", row: 3 })),
    ).toEqual([{ path: 'ingredients.3.ingredient_name', message: "ingredient 'Basil' already exists" }])
  })

  it('maps top-level fields and falls back to a form message', () => {
    expect(
      mapRecipeProblem(
        err({ errors: [{ loc: ['body', 'name'], msg: 'too long' }, { loc: ['body'], msg: 'bad' }] }),
      ),
    ).toEqual([
      { path: 'name', message: 'too long' },
      { path: null, message: 'bad' },
    ])
    expect(mapRecipeProblem(err({ detail: 'nope' }))).toEqual([{ path: null, message: 'nope' }])
  })
})
