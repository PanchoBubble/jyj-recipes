import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { delay, http, HttpResponse } from 'msw'

import { cookSummary, type CookResult } from '@/features/calendar/cook'
import { formatLongDay } from '@/features/calendar/dates'
import type { MealSlot, PlannedMeal } from '@/features/calendar/plan'
import { shoppingKeys } from '@/features/shopping/api'
import { stockKeys } from '@/features/stock/api'

import { renderApp } from './render'
import { API, alice, meAs, problem, server } from './server'

const MON = '2026-09-28'
const dinner: MealSlot = { id: 2, name: 'Dinner', position: 0, active: true }

function meal(id: number, name: string, overrides: Partial<PlannedMeal> = {}): PlannedMeal {
  return {
    id,
    date: MON,
    slot_id: dinner.id,
    recipe_id: id * 10,
    servings: 2,
    position: id,
    status: 'planned',
    cooked_at: null,
    cooked_by: null,
    created_by: 1,
    created_at: '2026-09-20T10:00:00Z',
    updated_at: '2026-09-20T10:00:00Z',
    recipe: {
      id: id * 10,
      name,
      photo_url: null,
      photo_thumb_url: null,
      default_servings: 2,
      archived_at: null,
    },
    slot: dinner,
    ...overrides,
  }
}

const flourImpact = {
  ingredient_id: 5,
  name: 'flour',
  delta_base: '-300.000',
  shortfall_base: '200.000',
  base_unit: 'g',
  display: { amount: '-300.000', unit: 'g' },
}
const eggImpact = {
  ingredient_id: 6,
  name: 'eggs',
  delta_base: '-2.000',
  shortfall_base: '0.000',
  base_unit: 'piece',
  display: { amount: '-2.000', unit: 'piece' },
}
const saltSkipped = {
  recipe_ingredient_id: 90,
  ingredient_id: 7,
  name: 'salt',
  amount: null,
  unit: 'pinch',
  reason: 'to_taste',
}

function mockApi(initial: PlannedMeal[], { failCook = false } = {}) {
  let meals = initial
  const requests: { method: string; path: string; body?: unknown }[] = []
  const setStatus = (id: number, changes: Partial<PlannedMeal>) => {
    meals = meals.map((m) => (m.id === id ? { ...m, ...changes } : m))
    return meals.find((m) => m.id === id)!
  }
  server.use(
    meAs(alice),
    http.get(`${API}/meal-slots`, () => HttpResponse.json([dinner])),
    http.get(`${API}/planned-meals`, () => HttpResponse.json(meals)),
    http.get(`${API}/recipes`, () =>
      HttpResponse.json({ items: [], total: 0, page: 1, page_size: 50 }),
    ),
    http.post(`${API}/planned-meals/:id/cook`, async ({ params }) => {
      requests.push({ method: 'POST', path: `/planned-meals/${params.id}/cook` })
      await delay(50)
      if (failCook) return problem(409, 'Conflict', 'meal is skipped')
      const saved = setStatus(Number(params.id), { status: 'cooked', cooked_at: '2026-09-28T19:00:00Z' })
      return HttpResponse.json({
        meal: saved,
        changed: true,
        stock: [flourImpact, eggImpact],
        skipped: [saltSkipped],
      } satisfies CookResult)
    }),
    http.post(`${API}/planned-meals/:id/uncook`, async ({ params }) => {
      requests.push({ method: 'POST', path: `/planned-meals/${params.id}/uncook` })
      const saved = setStatus(Number(params.id), { status: 'planned', cooked_at: null })
      return HttpResponse.json({
        meal: saved,
        changed: true,
        stock: [{ ...flourImpact, delta_base: '300.000', shortfall_base: '0.000', display: { amount: '300.000', unit: 'g' } }],
        skipped: [],
      } satisfies CookResult)
    }),
    http.patch(`${API}/planned-meals/:id`, async ({ request, params }) => {
      const body = (await request.json()) as Partial<PlannedMeal>
      requests.push({ method: 'PATCH', path: `/planned-meals/${params.id}`, body })
      return HttpResponse.json(setStatus(Number(params.id), body))
    }),
  )
  return requests
}

// The open drawer hides the page from the accessibility tree.
const cell = () =>
  screen.getByRole('region', { name: `${formatLongDay(MON)}, Dinner`, hidden: true })

async function openSheet(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(await screen.findByRole('button', { name: new RegExp(`^${name}, `) }))
  return screen.findByRole('dialog', { name })
}

describe('cookSummary', () => {
  const base = { meal: meal(1, 'Pancakes'), changed: true }

  it('lists what was used, shortfalls and untracked lines', () => {
    expect(cookSummary({ ...base, stock: [flourImpact, eggImpact], skipped: [saltSkipped] }, 'cook')).toEqual({
      title: 'Cooked Pancakes',
      lines: ['Used 300 g flour', 'Short 200 g flour', 'Used 2 pieces eggs', 'Stock not changed for salt (to taste)'],
      short: true,
    })
  })

  it('reports returned stock on undo and nothing when unchanged', () => {
    const returned = { ...flourImpact, delta_base: '1500.000', shortfall_base: '0.000', display: { amount: '1.5', unit: 'kg' } }
    expect(cookSummary({ ...base, stock: [returned], skipped: [] }, 'uncook')).toEqual({
      title: 'Undid cooking Pancakes',
      lines: ['Returned 1.5 kg flour'],
      short: false,
    })
    expect(cookSummary({ ...base, changed: false, stock: [], skipped: [] }, 'cook').title).toBe(
      'Pancakes was already cooked',
    )
  })
})

describe('meal status actions', () => {
  it('marks a meal cooked, shows the stock summary and refreshes stock', async () => {
    const requests = mockApi([meal(1, 'Pancakes')])
    const user = userEvent.setup()
    const { queryClient } = renderApp(`/calendar?week=${MON}`)
    queryClient.setQueryData(stockKeys.list(), [])
    queryClient.setQueryData(shoppingKeys.preview({ from: MON, to: MON }), {})

    const sheet = await openSheet(user, 'Pancakes')
    await user.click(within(sheet).getByRole('button', { name: 'Mark cooked' }))

    // Optimistic: the card turns cooked and the actions lock before the server answers.
    expect(
      within(cell()).getByRole('button', { name: 'Pancakes, 2 servings, cooked', hidden: true }),
    ).toBeInTheDocument()
    expect(within(sheet).getByRole('button', { name: 'Undo cooked' })).toBeDisabled()
    expect(within(sheet).getByRole('button', { name: 'Remove from plan' })).toBeDisabled()

    expect(await screen.findByText('Cooked Pancakes')).toBeInTheDocument()
    const changes = screen.getByRole('list', { name: 'Stock changes' })
    expect(within(changes).getByText('Used 300 g flour')).toBeInTheDocument()
    expect(within(changes).getByText('Short 200 g flour')).toBeInTheDocument()
    expect(within(changes).getByText('Stock not changed for salt (to taste)')).toBeInTheDocument()

    await waitFor(() => expect(within(sheet).getByRole('button', { name: 'Undo cooked' })).toBeEnabled())
    expect(requests).toContainEqual({ method: 'POST', path: '/planned-meals/1/cook' })
    expect(queryClient.getQueryState(stockKeys.list())?.isInvalidated).toBe(true)
    expect(queryClient.getQueryState(shoppingKeys.preview({ from: MON, to: MON }))?.isInvalidated).toBe(true)
  })

  it('undoes a cooked meal', async () => {
    const requests = mockApi([meal(1, 'Curry', { status: 'cooked', cooked_at: '2026-09-28T19:00:00Z' })])
    const user = userEvent.setup()
    renderApp(`/calendar?week=${MON}`)

    const sheet = await openSheet(user, 'Curry')
    await user.click(within(sheet).getByRole('button', { name: 'Undo cooked' }))

    expect(await screen.findByText('Undid cooking Curry')).toBeInTheDocument()
    expect(screen.getByText('Returned 300 g flour')).toBeInTheDocument()
    expect(
      within(cell()).getByRole('button', { name: 'Curry, 2 servings, planned', hidden: true }),
    ).toBeInTheDocument()
    expect(within(sheet).getByRole('button', { name: 'Mark cooked' })).toBeInTheDocument()
    expect(requests).toContainEqual({ method: 'POST', path: '/planned-meals/1/uncook' })
  })

  it('rolls the card back when cooking fails', async () => {
    mockApi([meal(1, 'Pancakes')], { failCook: true })
    const user = userEvent.setup()
    renderApp(`/calendar?week=${MON}`)

    const sheet = await openSheet(user, 'Pancakes')
    await user.click(within(sheet).getByRole('button', { name: 'Mark cooked' }))

    expect(await screen.findByText("Couldn't mark Pancakes as cooked")).toBeInTheDocument()
    expect(
      within(cell()).getByRole('button', { name: 'Pancakes, 2 servings, planned', hidden: true }),
    ).toBeInTheDocument()
    await waitFor(() => expect(within(sheet).getByRole('button', { name: 'Mark cooked' })).toBeEnabled())
  })

  it('skips and unskips a meal', async () => {
    const requests = mockApi([meal(1, 'Soup')])
    const user = userEvent.setup()
    renderApp(`/calendar?week=${MON}`)

    const sheet = await openSheet(user, 'Soup')
    await user.click(within(sheet).getByRole('button', { name: 'Skip' }))
    expect(
      within(cell()).getByRole('button', { name: 'Soup, 2 servings, skipped', hidden: true }),
    ).toBeInTheDocument()
    expect(requests).toContainEqual({ method: 'PATCH', path: '/planned-meals/1', body: { status: 'skipped' } })

    await user.click(await within(sheet).findByRole('button', { name: 'Unskip' }))
    await waitFor(() =>
      expect(
        within(cell()).getByRole('button', { name: 'Soup, 2 servings, planned', hidden: true }),
      ).toBeInTheDocument(),
    )
    expect(requests).toContainEqual({ method: 'PATCH', path: '/planned-meals/1', body: { status: 'planned' } })
  })
})
