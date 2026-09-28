import { QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import type { ReactNode } from 'react'
import { toast } from 'sonner'

import { plannedMealKeys } from '@/features/calendar/api'
import { addDays, formatLongDay, startOfWeek, todayIso } from '@/features/calendar/dates'
import { useDropAction } from '@/features/calendar/dnd'
import {
  applyMove,
  cellMeals,
  resolveDragEnd,
  type DragData,
  type DropData,
  type MealSlot,
  type PlannedMeal,
  type TrayRecipe,
} from '@/features/calendar/plan'
import type { RecipePage, RecipeSummary } from '@/features/recipes/api'
import { createQueryClient } from '@/lib/query'

import { renderApp } from './render'
import { API, alice, meAs, problem, server } from './server'

const MON = '2026-09-28'
const TUE = '2026-09-29'
const SUN = '2026-10-04'

const lunch: MealSlot = { id: 1, name: 'Lunch', position: 0, active: true }
const dinner: MealSlot = { id: 2, name: 'Dinner', position: 1, active: true }
const tea: MealSlot = { id: 3, name: 'Tea', position: 2, active: false }
const slots = [lunch, dinner, tea]

function recipe(id: number, name: string, default_servings = 2): TrayRecipe {
  return { id, name, photo_url: null, photo_thumb_url: null, default_servings }
}

const pancakes = recipe(7, 'Pancakes')
const soup = recipe(8, 'Soup', 4)
const curry = recipe(9, 'Curry', 3)

function meal(
  id: number,
  r: TrayRecipe,
  date: string,
  slot: MealSlot,
  position: number,
  overrides: Partial<PlannedMeal> = {},
): PlannedMeal {
  return {
    id,
    date,
    slot_id: slot.id,
    recipe_id: r.id,
    servings: r.default_servings,
    position,
    status: 'planned',
    cooked_at: null,
    cooked_by: null,
    created_by: 1,
    created_at: '2026-09-20T10:00:00Z',
    updated_at: '2026-09-20T10:00:00Z',
    recipe: { ...r, archived_at: null },
    slot,
    ...overrides,
  }
}

const week = (): PlannedMeal[] => [
  meal(11, pancakes, MON, lunch, 0),
  meal(12, soup, MON, lunch, 1),
  meal(13, curry, TUE, dinner, 0, { status: 'cooked', cooked_at: '2026-09-29T19:00:00Z' }),
]

const recipeData = (r: TrayRecipe): DragData => ({ type: 'recipe', recipe: r })
const mealData = (m: PlannedMeal): DragData => ({ type: 'meal', meal: m })
const cellDrop = (date: string, slotId: number, disabled = false): DropData => ({
  type: 'cell',
  cell: { date, slotId },
  disabled,
})
const mealDrop = (m: PlannedMeal): DropData => ({ type: 'meal', meal: m })

describe('resolveDragEnd', () => {
  const meals = week()
  const [m11, m12, m13] = meals

  it('creates a planned meal with the recipe default servings when a recipe lands on a cell', () => {
    expect(resolveDragEnd(recipeData(soup), cellDrop(TUE, lunch.id), meals)).toEqual({
      kind: 'create',
      cell: { date: TUE, slotId: lunch.id },
      recipe: soup,
      servings: 4,
    })
  })

  it("uses a meal card's cell when a recipe lands on the card", () => {
    expect(resolveDragEnd(recipeData(pancakes), mealDrop(m13), meals)).toMatchObject({
      kind: 'create',
      cell: { date: TUE, slotId: dinner.id },
    })
  })

  it('ignores drops on the tray, on disabled cells and on nothing', () => {
    expect(resolveDragEnd(recipeData(pancakes), { type: 'tray' }, meals)).toBeNull()
    expect(resolveDragEnd(recipeData(pancakes), cellDrop(MON, tea.id, true), meals)).toBeNull()
    expect(resolveDragEnd(mealData(m11), undefined, meals)).toBeNull()
    expect(resolveDragEnd(undefined, cellDrop(MON, lunch.id), meals)).toBeNull()
  })

  it('moves a meal to the end of another cell', () => {
    expect(resolveDragEnd(mealData(m11), cellDrop(TUE, dinner.id), meals)).toEqual({
      kind: 'move',
      id: 11,
      cell: { date: TUE, slotId: dinner.id },
      position: 1,
    })
  })

  it('moves a meal in front of the card it lands on', () => {
    expect(resolveDragEnd(mealData(m12), mealDrop(m13), meals)).toEqual({
      kind: 'move',
      id: 12,
      cell: { date: TUE, slotId: dinner.id },
      position: 0,
    })
  })

  it('reorders within a cell to the index of the card it lands on', () => {
    expect(resolveDragEnd(mealData(m11), mealDrop(m12), meals)).toEqual({
      kind: 'reorder',
      id: 11,
      cell: { date: MON, slotId: lunch.id },
      position: 1,
    })
    expect(resolveDragEnd(mealData(m12), cellDrop(MON, lunch.id), meals)).toBeNull()
    expect(resolveDragEnd(mealData(m11), mealDrop(m11), meals)).toBeNull()
  })
})

describe('applyMove', () => {
  it('renumbers both cells like the server does', () => {
    const next = applyMove(week(), 11, { date: TUE, slotId: dinner.id }, 0, dinner)
    expect(cellMeals(next, { date: MON, slotId: lunch.id }).map((m) => [m.id, m.position])).toEqual([
      [12, 0],
    ])
    expect(cellMeals(next, { date: TUE, slotId: dinner.id }).map((m) => [m.id, m.position])).toEqual([
      [11, 0],
      [13, 1],
    ])
    expect(next.find((m) => m.id === 11)?.slot).toBe(dinner)
  })

  it('clamps past-the-end positions to an append', () => {
    const next = applyMove(week(), 11, { date: MON, slotId: lunch.id }, 99)
    expect(cellMeals(next, { date: MON, slotId: lunch.id }).map((m) => m.id)).toEqual([12, 11])
  })
})

describe('drop handler', () => {
  const rangeKey = plannedMealKeys.range(MON, SUN)

  function setup() {
    const queryClient = createQueryClient()
    queryClient.setQueryData(rangeKey, week())
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useDropAction(slots), { wrapper })
    const onDragEnd = (active: DragData, over: DropData) => {
      const meals = queryClient.getQueryData<PlannedMeal[]>(rangeKey) ?? []
      const action = resolveDragEnd(active, over, meals)
      if (action) act(() => result.current(action, meals))
    }
    const cached = () => queryClient.getQueryData<PlannedMeal[]>(rangeKey) ?? []
    return { queryClient, onDragEnd, cached }
  }

  it('POSTs the recipe into the target cell and shows it right away', async () => {
    let body: unknown
    let release!: () => void
    const gate = new Promise<void>((resolve) => (release = resolve))
    server.use(
      http.post(`${API}/planned-meals`, async ({ request }) => {
        body = await request.json()
        await gate
        return HttpResponse.json(meal(20, pancakes, TUE, lunch, 0), { status: 201 })
      }),
    )
    const { onDragEnd, cached } = setup()

    onDragEnd(recipeData(pancakes), cellDrop(TUE, lunch.id))

    await waitFor(() =>
      expect(body).toEqual({ date: TUE, slot_id: lunch.id, recipe_id: 7, servings: 2 }),
    )
    const draft = cellMeals(cached(), { date: TUE, slotId: lunch.id })
    expect(draft).toHaveLength(1)
    expect(draft[0].id).toBeLessThan(0)
    expect(draft[0].recipe.name).toBe('Pancakes')
    release()
  })

  it('PATCHes date, slot and position for a move between cells', async () => {
    let body: unknown
    server.use(
      http.patch(`${API}/planned-meals/12`, async ({ request }) => {
        body = await request.json()
        return HttpResponse.json(meal(12, soup, TUE, dinner, 0))
      }),
    )
    const { onDragEnd, cached } = setup()
    const m12 = week()[1]
    const m13 = week()[2]

    onDragEnd(mealData(m12), mealDrop(m13))

    await waitFor(() =>
      expect(cellMeals(cached(), { date: TUE, slotId: dinner.id }).map((m) => m.id)).toEqual([12, 13]),
    )
    await waitFor(() => expect(body).toEqual({ date: TUE, slot_id: dinner.id, position: 0 }))
  })

  it('PATCHes only the position for a reorder within a cell', async () => {
    let body: unknown
    server.use(
      http.patch(`${API}/planned-meals/11`, async ({ request }) => {
        body = await request.json()
        return HttpResponse.json(meal(11, pancakes, MON, lunch, 1))
      }),
    )
    const { onDragEnd, cached } = setup()

    onDragEnd(mealData(week()[0]), mealDrop(week()[1]))

    await waitFor(() =>
      expect(cellMeals(cached(), { date: MON, slotId: lunch.id }).map((m) => m.id)).toEqual([12, 11]),
    )
    await waitFor(() => expect(body).toEqual({ position: 1 }))
  })

  it('rolls back and toasts when the server rejects the move', async () => {
    const toastError = vi.spyOn(toast, 'error').mockImplementation(() => 0)
    let release!: () => void
    const gate = new Promise<void>((resolve) => (release = resolve))
    server.use(
      http.patch(`${API}/planned-meals/11`, async () => {
        await gate
        return problem(422, 'Unprocessable', "meal slot 'Dinner' is inactive")
      }),
    )
    const { onDragEnd, cached, queryClient } = setup()
    const before = cached()

    onDragEnd(mealData(week()[0]), cellDrop(TUE, dinner.id))
    await waitFor(() =>
      expect(cellMeals(cached(), { date: TUE, slotId: dinner.id }).map((m) => m.id)).toEqual([13, 11]),
    )
    release()

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Couldn't update Pancakes", {
        description: "meal slot 'Dinner' is inactive",
      }),
    )
    expect(cached()).toEqual(before)
    await waitFor(() => expect(queryClient.getQueryState(rangeKey)?.isInvalidated).toBe(true))
    toastError.mockRestore()
  })
})

// Page flows ------------------------------------------------------------------

function summary(r: TrayRecipe): RecipeSummary {
  return {
    ...r,
    description: null,
    ingredient_count: 0,
    created_by: 1,
    created_at: '2026-09-01T10:00:00Z',
    updated_at: '2026-09-01T10:00:00Z',
    archived_at: null,
  }
}

function recipePage(items: TrayRecipe[]): RecipePage {
  return { items: items.map(summary), total: items.length, page: 1, page_size: 20 }
}

function mockCalendarApi(initial = week()) {
  let meals = initial
  const requests: { method: string; path: string; body?: unknown }[] = []
  server.use(
    meAs(alice),
    http.get(`${API}/meal-slots`, () => HttpResponse.json(slots)),
    http.get(`${API}/planned-meals`, ({ request }) => {
      const url = new URL(request.url)
      requests.push({ method: 'GET', path: `/planned-meals${url.search}` })
      const from = url.searchParams.get('from')!
      const to = url.searchParams.get('to')!
      return HttpResponse.json(meals.filter((m) => m.date >= from && m.date <= to))
    }),
    http.get(`${API}/recipes`, () => HttpResponse.json(recipePage([pancakes, soup, curry]))),
    http.post(`${API}/planned-meals`, async ({ request }) => {
      const body = (await request.json()) as { date: string; slot_id: number; recipe_id: number; servings: number }
      requests.push({ method: 'POST', path: '/planned-meals', body })
      const r = [pancakes, soup, curry].find((x) => x.id === body.recipe_id)!
      const slot = slots.find((s) => s.id === body.slot_id)!
      const created = meal(100 + meals.length, r, body.date, slot, 99, { servings: body.servings })
      meals = [...meals, created]
      return HttpResponse.json(created, { status: 201 })
    }),
    http.patch(`${API}/planned-meals/:id`, async ({ request, params }) => {
      const body = (await request.json()) as Partial<PlannedMeal>
      requests.push({ method: 'PATCH', path: `/planned-meals/${params.id}`, body })
      meals = meals.map((m) => (m.id === Number(params.id) ? { ...m, ...body } : m))
      return HttpResponse.json(meals.find((m) => m.id === Number(params.id)))
    }),
    http.delete(`${API}/planned-meals/:id`, ({ params }) => {
      requests.push({ method: 'DELETE', path: `/planned-meals/${params.id}` })
      meals = meals.filter((m) => m.id !== Number(params.id))
      return new HttpResponse(null, { status: 204 })
    }),
  )
  return requests
}

const cellLabel = (date: string, slot: MealSlot) => `${formatLongDay(date)}, ${slot.name}`

describe('calendar page', () => {
  it('shows the week as days with active slot cells and their meals', async () => {
    mockCalendarApi()
    renderApp(`/calendar?week=${MON}`)

    const monLunch = await screen.findByRole('region', { name: cellLabel(MON, lunch) })
    expect(
      within(monLunch)
        .getAllByRole('button', { name: /servings/ })
        .map((b) => b.getAttribute('aria-label')),
    ).toEqual(['Pancakes, 2 servings, planned', 'Soup, 4 servings, planned'])
    expect(screen.getByRole('button', { name: 'Curry, 3 servings, cooked' })).toBeInTheDocument()
    expect(screen.getAllByRole('region', { name: /, (Lunch|Dinner)$/ })).toHaveLength(14)
    expect(screen.queryByRole('region', { name: /, Tea$/ })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /^Move / })).toHaveLength(3)
  })

  it('lays the week out as slot rows across day columns, keyed by date and slot', async () => {
    mockCalendarApi()
    renderApp(`/calendar?week=${MON}`)

    const grid = await screen.findByTestId('week-grid')
    const days = Array.from({ length: 7 }, (_, i) => addDays(MON, i))
    expect(Array.from(grid.querySelectorAll('[data-cell]'), (c) => c.getAttribute('data-cell'))).toEqual([
      ...days.map((d) => `cell:${d}:${lunch.id}`),
      ...days.map((d) => `cell:${d}:${dinner.id}`),
    ])
    expect(
      Array.from(grid.querySelectorAll('[data-slot-label]'), (l) => l.textContent),
    ).toEqual(['Lunch', 'Dinner'])
    expect(
      Array.from(screen.getByTestId('day-strip').querySelectorAll('[data-day]'), (d) =>
        d.getAttribute('data-day'),
      ),
    ).toEqual(days)
  })

  it('keeps an inactive slot row while the week still has meals in it', async () => {
    mockCalendarApi([...week(), meal(14, soup, TUE, tea, 0)])
    renderApp(`/calendar?week=${MON}`)

    const tueTea = await screen.findByRole('region', { name: cellLabel(TUE, tea) })
    expect(within(tueTea).getByRole('button', { name: /^Soup, / })).toBeInTheDocument()
    expect(within(tueTea).queryByRole('button', { name: /^Add a recipe/ })).not.toBeInTheDocument()
    expect(screen.getAllByRole('region', { name: /, Tea$/ })).toHaveLength(7)
    expect(screen.getByText('(inactive)')).toBeInTheDocument()
  })

  it("highlights today's column and scrolls it into view", async () => {
    mockCalendarApi()
    const user = userEvent.setup()
    renderApp('/calendar')

    const today = todayIso()
    const grid = await screen.findByTestId('week-grid')
    const header = screen.getByTestId('day-strip').querySelector('[aria-current="date"]')
    expect(header?.getAttribute('data-day')).toBe(today)

    const scrollTo = vi.fn()
    grid.scrollTo = scrollTo
    await user.click(screen.getByRole('button', { name: 'Today' }))
    expect(scrollTo).toHaveBeenCalledWith(expect.objectContaining({ behavior: 'smooth' }))
  })

  it('changes week with the prev/next buttons', async () => {
    const requests = mockCalendarApi()
    const user = userEvent.setup()
    const { router } = renderApp(`/calendar?week=${MON}`)
    await screen.findByRole('region', { name: cellLabel(MON, lunch) })

    await user.click(screen.getByRole('button', { name: 'Next week' }))
    await screen.findByRole('region', { name: cellLabel(addDays(MON, 7), lunch) })
    expect(router.state.location.search).toBe(`?week=${addDays(MON, 7)}`)
    expect(requests.map((r) => r.path)).toContain(
      `/planned-meals?from=${addDays(MON, 7)}&to=${addDays(SUN, 7)}`,
    )

    await user.click(screen.getByRole('button', { name: 'Today' }))
    expect(router.state.location.search).toBe('')
    expect(
      await screen.findByRole('region', { name: cellLabel(startOfWeek(todayIso()), lunch) }),
    ).toBeInTheDocument()
  })

  it('adds a recipe by tapping an empty cell', async () => {
    const requests = mockCalendarApi()
    const user = userEvent.setup()
    renderApp(`/calendar?week=${MON}`)

    await user.click(await screen.findByRole('button', { name: `Add a recipe to ${cellLabel(TUE, lunch)}` }))
    const picker = await screen.findByRole('dialog', { name: 'Add to Lunch' })
    await user.click(await within(picker).findByRole('button', { name: /Soup/ }))

    const tueLunch = screen.getByRole('region', { name: cellLabel(TUE, lunch) })
    expect(await within(tueLunch).findByRole('button', { name: /^Soup, 4 servings/ })).toBeInTheDocument()
    expect(requests.find((r) => r.method === 'POST')?.body).toEqual({
      date: TUE,
      slot_id: lunch.id,
      recipe_id: soup.id,
      servings: 4,
    })
  })

  it('edits servings and removes a meal from its sheet', async () => {
    const requests = mockCalendarApi()
    const user = userEvent.setup()
    renderApp(`/calendar?week=${MON}`)

    await user.click(await screen.findByRole('button', { name: 'Pancakes, 2 servings, planned' }))
    const sheet = await screen.findByRole('dialog', { name: 'Pancakes' })
    expect(within(sheet).getByRole('link', { name: 'Open recipe' })).toHaveAttribute('href', '/recipes/7')
    await user.click(within(sheet).getByRole('button', { name: 'More servings' }))
    await waitFor(() =>
      expect(requests.find((r) => r.method === 'PATCH')).toEqual({
        method: 'PATCH',
        path: '/planned-meals/11',
        body: { servings: 3 },
      }),
    )
    expect(
      await screen.findByRole('button', { name: 'Pancakes, 3 servings, planned', hidden: true }),
    ).toBeInTheDocument()

    await user.click(within(sheet).getByRole('button', { name: 'Remove from plan' }))
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /^Pancakes, / })).not.toBeInTheDocument(),
    )
    expect(requests).toContainEqual({ method: 'DELETE', path: '/planned-meals/11' })
  })

  it('asks to uncook before removing or rescaling a cooked meal', async () => {
    mockCalendarApi()
    const user = userEvent.setup()
    renderApp(`/calendar?week=${MON}`)

    await user.click(await screen.findByRole('button', { name: 'Curry, 3 servings, cooked' }))
    const sheet = await screen.findByRole('dialog', { name: 'Curry' })
    expect(within(sheet).getByRole('button', { name: 'Remove from plan' })).toBeDisabled()
    expect(within(sheet).getByText('Uncook first to remove this meal.')).toBeInTheDocument()
    expect(within(sheet).getByRole('button', { name: 'More servings' })).toBeDisabled()
  })
})
