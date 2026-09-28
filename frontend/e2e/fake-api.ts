import type { Page, Route } from '@playwright/test'

export const WEEK = '2026-09-28'

const slots = [
  { id: 1, name: 'Lunch', position: 0, active: true },
  { id: 2, name: 'Dinner', position: 1, active: true },
]

const recipes = [
  { id: 7, name: 'Pancakes', default_servings: 2 },
  { id: 8, name: 'Lentil soup', default_servings: 4 },
  { id: 9, name: 'Green curry', default_servings: 3 },
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

function plannedMeal(id: number, recipeId: number, date: string, slotId: number, servings?: number) {
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
    slot: slots.find((s) => s.id === slotId)!,
  }
}

export interface ApiCall {
  method: string
  path: string
  body: unknown
}

/** In-memory stand-in for the backend, served through route interception. */
export async function mockApi(page: Page) {
  const calls: ApiCall[] = []
  let meals = [plannedMeal(1, 8, WEEK, 1)]
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
        const { date, slot_id, recipe_id, servings } = body as Record<string, never>
        const created = plannedMeal(nextId++, recipe_id, date, slot_id, servings)
        created.position = meals.filter((m) => m.date === date && m.slot_id === slot_id).length
        meals = [...meals, created]
        return json(route, created, 201)
      }
      return json(route, { type: 'about:blank', title: 'Not Found', status: 404 }, 404)
    },
  )
  return calls
}
