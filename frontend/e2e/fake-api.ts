import type { Page, Route } from '@playwright/test'

export const WEEK = '2026-09-28'

const slots = [
  { id: 1, name: 'Lunch', position: 0, active: true },
  { id: 2, name: 'Dinner', position: 1, active: true },
  { id: 3, name: 'Tea', position: 2, active: true },
  { id: 4, name: 'Supper', position: 3, active: true },
]

// Enough recipes that the docked panel scrolls on its own.
const recipes = [
  { id: 7, name: 'Pancakes', default_servings: 2 },
  { id: 8, name: 'Lentil soup', default_servings: 4 },
  { id: 9, name: 'Green curry', default_servings: 3 },
  ...['Risotto', 'Tacos', 'Shakshuka', 'Paella', 'Ramen', 'Chili', 'Frittata']
    .concat(['Dal', 'Gnocchi', 'Pho', 'Stew', 'Salad', 'Pie', 'Bibimbap'])
    .map((name, i) => ({ id: 20 + i, name, default_servings: 2 })),
].map((r) => ({
  ...r,
  description: null,
  ingredient_count: 0,
  photo_url: null,
  photo_thumb_url: null,
  created_by: 1,
  created_at: '2026-09-01T10:00:00Z',
  updated_at: '2026-09-01T10:00:00Z',
  archived_at: null,
}))

function plannedMeal(id: number, recipeId: number, date: string, slotId: number | null, servings?: number) {
  const recipe = recipes.find((r) => r.id === recipeId)!
  return {
    id,
    date,
    slot_id: slotId,
    recipe_id: recipeId,
    servings: servings ?? recipe.default_servings,
    position: 0,
    status: 'planned',
    cooked_at: null,
    cooked_by: null,
    created_by: 1,
    created_at: '2026-09-20T10:00:00Z',
    updated_at: '2026-09-20T10:00:00Z',
    recipe: {
      id: recipe.id,
      name: recipe.name,
      photo_url: null,
      photo_thumb_url: null,
      default_servings: recipe.default_servings,
      archived_at: null,
    },
    slot: slots.find((s) => s.id === slotId) ?? null,
  }
}

type Meal = ReturnType<typeof plannedMeal>

/** Server ordering: position within the day, then id; renumbered 0..n after every write. */
function byDay(meals: Meal[], date: string) {
  return meals.filter((m) => m.date === date).sort((a, b) => a.position - b.position || a.id - b.id)
}

function place(meals: Meal[], meal: Meal, position: number | undefined) {
  const others = meals.filter((m) => m.id !== meal.id)
  const day = byDay(others, meal.date)
  day.splice(Math.min(position ?? day.length, day.length), 0, meal)
  day.forEach((m, i) => (m.position = i))
  for (const date of new Set(others.map((m) => m.date))) {
    if (date !== meal.date) byDay(others, date).forEach((m, i) => (m.position = i))
  }
  return [...others.filter((m) => m.date !== meal.date), ...day]
}

export interface ApiCall {
  method: string
  path: string
  body: unknown
}

/** In-memory stand-in for the backend, served through route interception. */
export async function mockApi(page: Page) {
  const calls: ApiCall[] = []
  // Enough meals on Monday that its column scrolls inside the week grid on a tall phone too.
  let meals = [
    plannedMeal(1, 8, WEEK, 1),
    plannedMeal(2, 9, WEEK, null),
    plannedMeal(3, 7, WEEK, 3),
    plannedMeal(4, 20, WEEK, null),
    plannedMeal(5, 21, WEEK, 4),
    plannedMeal(6, 22, WEEK, null),
  ].map((m, position) => ({ ...m, position }))
  let nextId = 100

  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

  await page.route(
    (url) => url.pathname.startsWith('/api/v1/'),
    async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      const path = url.pathname.replace('/api/v1', '')
      const method = request.method()
      const body = request.postData() ? request.postDataJSON() : undefined
      if (method !== 'GET') calls.push({ method, path, body })

      if (method === 'GET' && path === '/auth/me') {
        return json(route, { id: 1, username: 'alice', display_name: 'Alice' })
      }
      if (method === 'GET' && path === '/meal-slots') return json(route, slots)
      if (method === 'GET' && path === '/recipes') {
        return json(route, { items: recipes, total: recipes.length, page: 1, page_size: 20 })
      }
      if (method === 'GET' && path === '/planned-meals') {
        const from = url.searchParams.get('from')!
        const to = url.searchParams.get('to')!
        return json(route, meals.filter((m) => m.date >= from && m.date <= to))
      }
      if (method === 'POST' && path === '/planned-meals') {
        const { date, slot_id, recipe_id, servings, position } = body as Record<string, never>
        const created = plannedMeal(nextId++, recipe_id, date, slot_id ?? null, servings)
        meals = place(meals, created, position)
        return json(route, created, 201)
      }
      const patch = /^\/planned-meals\/(\d+)$/.exec(path)
      if (method === 'PATCH' && patch) {
        const meal = meals.find((m) => m.id === Number(patch[1]))
        if (!meal) return json(route, { type: 'about:blank', title: 'Not Found', status: 404 }, 404)
        const { date, position, ...fields } = body as Partial<Meal>
        const moved = { ...meal, ...fields, date: date ?? meal.date }
        meals =
          date !== undefined || position !== undefined
            ? place(meals, moved, position)
            : meals.map((m) => (m.id === meal.id ? moved : m))
        return json(route, meals.find((m) => m.id === meal.id))
      }
      return json(route, { type: 'about:blank', title: 'Not Found', status: 404 }, 404)
    },
  )
  return calls
}
