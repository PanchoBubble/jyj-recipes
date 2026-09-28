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
  dayMeals,
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
  position: number,
  overrides: Partial<PlannedMeal> = {},
): PlannedMeal {
  return {
    id,
    date,
    slot_id: null,
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
    slot: null,
    ...overrides,
  }
}

const labelled = (slot: MealSlot) => ({ slot_id: slot.id, slot })

const week = (): PlannedMeal[] => [
  meal(11, pancakes, MON, 0, labelled(lunch)),
  meal(12, soup, MON, 1),
  meal(13, curry, TUE, 0, { status: 'cooked', cooked_at: '2026-09-29T19:00:00Z' }),
]

const recipeData = (r: TrayRecipe): DragData => ({ type: 'recipe', recipe: r })
const mealData = (m: PlannedMeal): DragData => ({ type: 'meal', meal: m })
const dayDrop = (date: string): DropData => ({ type: 'day', date })
const mealDrop = (m: PlannedMeal): DropData => ({ type: 'meal', meal: m })
const ids = (meals: PlannedMeal[], date: string) => dayMeals(meals, date).map((m) => m.id)

describe('resolveDragEnd', () => {
  const meals = week()
  const [m11, m12, m13] = meals

  it('appends a recipe dropped on a day, with its default servings', () => {
    expect(resolveDragEnd(recipeData(soup), dayDrop(MON), meals)).toEqual({
      kind: 'create',
      date: MON,
      position: 2,
      recipe: soup,
      servings: 4,
    })
  })

  it('inserts a recipe in front of the card it lands on', () => {
    expect(resolveDragEnd(recipeData(pancakes), mealDrop(m12), meals)).toMatchObject({
      kind: 'create',
      date: MON,
      position: 1,
    })
  })

  it('ignores drops on the recipe panel and on nothing', () => {
    expect(resolveDragEnd(recipeData(pancakes), { type: 'tray' }, meals)).toBeNull()
    expect(resolveDragEnd(mealData(m11), { type: 'tray' }, meals)).toBeNull()
    expect(resolveDragEnd(mealData(m11), undefined, meals)).toBeNull()
    expect(resolveDragEnd(undefined, dayDrop(MON), meals)).toBeNull()
  })

  it('moves a meal to the end of another day', () => {
    expect(resolveDragEnd(mealData(m11), dayDrop(TUE), meals)).toEqual({
      kind: 'move',
      id: 11,
      date: TUE,
      position: 1,
    })
  })

  it('moves a meal in front of the card it lands on in another day', () => {
    expect(resolveDragEnd(mealData(m12), mealDrop(m13), meals)).toEqual({
      kind: 'move',
      id: 12,
      date: TUE,
      position: 0,
    })
  })

  it('reorders within a day to the index of the card it lands on', () => {
    expect(resolveDragEnd(mealData(m11), mealDrop(m12), meals)).toEqual({
      kind: 'reorder',
      id: 11,
      date: MON,
      position: 1,
    })
    expect(resolveDragEnd(mealData(m11), dayDrop(MON), meals)).toEqual({
      kind: 'reorder',
      id: 11,
      date: MON,
      position: 1,
    })
    expect(resolveDragEnd(mealData(m12), dayDrop(MON), meals)).toBeNull()
    expect(resolveDragEnd(mealData(m11), mealDrop(m11), meals)).toBeNull()
  })
})

describe('applyMove', () => {
  it('renumbers both days like the server does, keeping the label', () => {
    const next = applyMove(week(), 11, TUE, 0)
    expect(dayMeals(next, MON).map((m) => [m.id, m.position])).toEqual([[12, 0]])
    expect(dayMeals(next, TUE).map((m) => [m.id, m.position])).toEqual([
      [11, 0],
      [13, 1],
    ])
    expect(next.find((m) => m.id === 11)?.slot).toBe(lunch)
  })

  it('clamps past-the-end positions to an append', () => {
    expect(ids(applyMove(week(), 11, MON, 99), MON)).toEqual([12, 11])
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
    const { result } = renderHook(() => useDropAction(), { wrapper })
    const onDragEnd = (active: DragData, over: DropData) => {
      const meals = queryClient.getQueryData<PlannedMeal[]>(rangeKey) ?? []
      const action = resolveDragEnd(active, over, meals)
      if (action) act(() => result.current(action, meals))
    }
    const cached = () => queryClient.getQueryData<PlannedMeal[]>(rangeKey) ?? []
    return { queryClient, onDragEnd, cached }
  }

  it('POSTs the recipe at the drop index with no label and shows it right away', async () => {
    let body: unknown
    let release!: () => void
    const gate = new Promise<void>((resolve) => (release = resolve))
    server.use(
      http.post(`${API}/planned-meals`, async ({ request }) => {
        body = await request.json()
        await gate
        return HttpResponse.json(meal(20, curry, MON, 1), { status: 201 })
      }),
    )
    const { onDragEnd, cached } = setup()

    onDragEnd(recipeData(curry), mealDrop(week()[1]))

    await waitFor(() =>
      expect(body).toEqual({ date: MON, recipe_id: curry.id, servings: 3, position: 1 }),
    )
    const day = dayMeals(cached(), MON)
    expect(day.map((m) => m.recipe.name)).toEqual(['Pancakes', 'Curry', 'Soup'])
    expect(day[1].id).toBeLessThan(0)
    expect(day[1].slot).toBeNull()
    release()
  })

  it('POSTs at the end of an empty day', async () => {
    let body: unknown
    server.use(
      http.post(`${API}/planned-meals`, async ({ request }) => {
        body = await request.json()
        return HttpResponse.json(meal(21, soup, SUN, 0), { status: 201 })
      }),
    )
    const { onDragEnd } = setup()

    onDragEnd(recipeData(soup), dayDrop(SUN))

    await waitFor(() => expect(body).toEqual({ date: SUN, recipe_id: soup.id, servings: 4, position: 0 }))
  })

  it('PATCHes date and position for a move across days', async () => {
    let body: unknown
    server.use(
      http.patch(`${API}/planned-meals/12`, async ({ request }) => {
        body = await request.json()
        return HttpResponse.json(meal(12, soup, TUE, 0))
      }),
    )
    const { onDragEnd, cached } = setup()
    const [, m12, m13] = week()

    onDragEnd(mealData(m12), mealDrop(m13))

    await waitFor(() => expect(ids(cached(), TUE)).toEqual([12, 13]))
    await waitFor(() => expect(body).toEqual({ date: TUE, position: 0 }))
  })

  it('PATCHes only the position for a reorder within a day', async () => {
    let body: unknown
    server.use(
      http.patch(`${API}/planned-meals/11`, async ({ request }) => {
        body = await request.json()
        return HttpResponse.json(meal(11, pancakes, MON, 1, labelled(lunch)))
      }),
    )
    const { onDragEnd, cached } = setup()

    onDragEnd(mealData(week()[0]), mealDrop(week()[1]))

    await waitFor(() => expect(ids(cached(), MON)).toEqual([12, 11]))
    await waitFor(() => expect(body).toEqual({ position: 1 }))
  })

  it('rolls back and toasts when the server rejects the move', async () => {
    const toastError = vi.spyOn(toast, 'error').mockImplementation(() => 0)
    let release!: () => void
    const gate = new Promise<void>((resolve) => (release = resolve))
    server.use(
      http.patch(`${API}/planned-meals/11`, async () => {
        await gate
        return problem(409, 'Conflict', 'meal is cooked')
      }),
    )
    const { onDragEnd, cached, queryClient } = setup()
    const before = cached()

    onDragEnd(mealData(week()[0]), dayDrop(TUE))
    await waitFor(() => expect(ids(cached(), TUE)).toEqual([13, 11]))
    release()

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Couldn't update Pancakes", {
        description: 'meal is cooked',
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
  const withSlot = (m: PlannedMeal): PlannedMeal => ({
    ...m,
    slot: m.slot_id === null ? null : (slots.find((s) => s.id === m.slot_id) ?? null),
  })
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
      const body = (await request.json()) as { date: string; recipe_id: number; servings: number }
      requests.push({ method: 'POST', path: '/planned-meals', body })
      const r = [pancakes, soup, curry].find((x) => x.id === body.recipe_id)!
      const created = meal(100 + meals.length, r, body.date, 99, { servings: body.servings })
      meals = [...meals, created]
      return HttpResponse.json(created, { status: 201 })
    }),
    http.patch(`${API}/planned-meals/:id`, async ({ request, params }) => {
      const body = (await request.json()) as Partial<PlannedMeal>
      requests.push({ method: 'PATCH', path: `/planned-meals/${params.id}`, body })
      meals = meals.map((m) => (m.id === Number(params.id) ? withSlot({ ...m, ...body }) : m))
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

const dayRegion = (date: string, hidden = false) =>
  screen.getByRole('region', { name: formatLongDay(date), hidden })

describe('calendar page', () => {
  it('shows each day as one column of meals in position order, labels as tags', async () => {
    mockCalendarApi()
    renderApp(`/calendar?week=${MON}`)

    const monday = await screen.findByRole('region', { name: formatLongDay(MON) })
    expect(
      within(monday)
        .getAllByRole('button', { name: /servings/ })
        .map((b) => b.getAttribute('aria-label')),
    ).toEqual(['Pancakes, Lunch, 2 servings, planned', 'Soup, 4 servings, planned'])
    expect(within(monday).getByText('Lunch')).toHaveAttribute('data-meal-label')
    expect(screen.getByRole('button', { name: 'Curry, 3 servings, cooked' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /^Move / })).toHaveLength(3)
  })

  it('lays out seven day columns under a matching day strip, with no slot rows', async () => {
    mockCalendarApi()
    renderApp(`/calendar?week=${MON}`)

    const grid = await screen.findByTestId('week-grid')
    const days = Array.from({ length: 7 }, (_, i) => addDays(MON, i))
    expect(
      Array.from(grid.querySelectorAll('[data-day-column]'), (c) => c.getAttribute('data-day-column')),
    ).toEqual(days)
    expect(grid.querySelector('[data-slot-label]')).toBeNull()
    expect(
      Array.from(screen.getByTestId('day-strip').querySelectorAll('[data-day]'), (d) =>
        d.getAttribute('data-day'),
      ),
    ).toEqual(days)
  })

  it('docks a searchable recipe panel with draggable cards under the week', async () => {
    mockCalendarApi()
    renderApp(`/calendar?week=${MON}`)

    const panel = await screen.findByRole('complementary', { name: 'Recipes' })
    expect(panel).toHaveAttribute('data-bottom-dock')
    expect(within(panel).getByRole('searchbox', { name: 'Search recipes' })).toBeInTheDocument()
    expect(
      await within(panel).findAllByRole('button', { name: /^Drag .* onto the calendar$/ }),
    ).toHaveLength(3)
    expect(screen.queryByRole('button', { name: 'Recipes' })).not.toBeInTheDocument()
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
    await screen.findByRole('region', { name: formatLongDay(MON) })

    await user.click(screen.getByRole('button', { name: 'Next week' }))
    await screen.findByRole('region', { name: formatLongDay(addDays(MON, 7)) })
    expect(router.state.location.search).toBe(`?week=${addDays(MON, 7)}`)
    expect(requests.map((r) => r.path)).toContain(
      `/planned-meals?from=${addDays(MON, 7)}&to=${addDays(SUN, 7)}`,
    )

    await user.click(screen.getByRole('button', { name: 'Today' }))
    expect(router.state.location.search).toBe('')
    expect(
      await screen.findByRole('region', { name: formatLongDay(startOfWeek(todayIso())) }),
    ).toBeInTheDocument()
  })

  it('adds a recipe to the end of a day by tapping its empty area', async () => {
    const requests = mockCalendarApi()
    const user = userEvent.setup()
    renderApp(`/calendar?week=${MON}`)

    await user.click(
      await screen.findByRole('button', { name: `Add a recipe to ${formatLongDay(TUE)}` }),
    )
    const picker = await screen.findByRole('dialog', { name: `Add to ${formatLongDay(TUE)}` })
    await user.click(await within(picker).findByRole('button', { name: /Soup/ }))

    expect(
      await within(dayRegion(TUE)).findByRole('button', { name: /^Soup, 4 servings/ }),
    ).toBeInTheDocument()
    expect(requests.find((r) => r.method === 'POST')?.body).toEqual({
      date: TUE,
      recipe_id: soup.id,
      servings: 4,
    })
  })

  it('sets and clears the label from the meal sheet', async () => {
    const requests = mockCalendarApi()
    const user = userEvent.setup()
    renderApp(`/calendar?week=${MON}`)

    await user.click(await screen.findByRole('button', { name: 'Soup, 4 servings, planned' }))
    const sheet = await screen.findByRole('dialog', { name: 'Soup' })
    const select = within(sheet).getByRole('combobox', { name: 'Label' })
    expect(select).toHaveValue('')
    // Inactive labels are not offered for a meal that doesn't have one.
    expect(within(select).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'None',
      'Lunch',
      'Dinner',
    ])

    await user.selectOptions(select, 'Dinner')
    await waitFor(() =>
      expect(requests.filter((r) => r.method === 'PATCH')).toEqual([
        { method: 'PATCH', path: '/planned-meals/12', body: { slot_id: dinner.id } },
      ]),
    )
    expect(
      await within(dayRegion(MON, true)).findByRole('button', {
        name: 'Soup, Dinner, 4 servings, planned',
        hidden: true,
      }),
    ).toBeInTheDocument()

    await user.selectOptions(select, 'None')
    await waitFor(() =>
      expect(requests.filter((r) => r.method === 'PATCH').at(-1)).toEqual({
        method: 'PATCH',
        path: '/planned-meals/12',
        body: { slot_id: null },
      }),
    )
    expect(
      await within(dayRegion(MON, true)).findByRole('button', {
        name: 'Soup, 4 servings, planned',
        hidden: true,
      }),
    ).toBeInTheDocument()
    expect(select).toHaveValue('')
  })

  it('edits servings and removes a meal from its sheet', async () => {
    const requests = mockCalendarApi()
    const user = userEvent.setup()
    renderApp(`/calendar?week=${MON}`)

    await user.click(await screen.findByRole('button', { name: 'Soup, 4 servings, planned' }))
    const sheet = await screen.findByRole('dialog', { name: 'Soup' })
    expect(within(sheet).getByRole('link', { name: 'Open recipe' })).toHaveAttribute('href', '/recipes/8')
    await user.click(within(sheet).getByRole('button', { name: 'More servings' }))
    await waitFor(() =>
      expect(requests.find((r) => r.method === 'PATCH')).toEqual({
        method: 'PATCH',
        path: '/planned-meals/12',
        body: { servings: 5 },
      }),
    )
    expect(
      await screen.findByRole('button', { name: 'Soup, 5 servings, planned', hidden: true }),
    ).toBeInTheDocument()

    await user.click(within(sheet).getByRole('button', { name: 'Remove from plan' }))
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /^Soup, / })).not.toBeInTheDocument(),
    )
    expect(requests).toContainEqual({ method: 'DELETE', path: '/planned-meals/12' })
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
