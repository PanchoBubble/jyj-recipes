import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import type { Ingredient, Unit } from '@/features/ingredients/api'
import type {
  PreviewItem,
  ShoppingList,
  ShoppingListItem,
  ShoppingListSummary,
  ShoppingPreview,
} from '@/features/shopping/api'
import { PRESETS } from '@/features/shopping/dates'
import { stockKeys } from '@/features/stock/api'

import { renderApp } from './render'
import { API, alice, meAs, problem, server } from './server'

const units: Unit[] = [
  { code: 'g', dimension: 'mass', to_base: '1' },
  { code: 'kg', dimension: 'mass', to_base: '1000' },
  { code: 'ml', dimension: 'volume', to_base: '1' },
  { code: 'l', dimension: 'volume', to_base: '1000' },
  { code: 'cup', dimension: 'volume', to_base: '240' },
  { code: 'piece', dimension: 'count', to_base: '1' },
]

function previewItem(overrides: Partial<PreviewItem> & Pick<PreviewItem, 'ingredient_id' | 'ingredient_name'>): PreviewItem {
  return {
    category: null,
    dimension: 'mass',
    base_unit: 'g',
    required_base: '0.000',
    stock_base: '0.000',
    reserved_base: '0.000',
    available_base: '0.000',
    to_buy_base: '0.000',
    display: { amount: '0.000', unit: 'g' },
    nothing_to_buy: true,
    contributions: [],
    unconverted: [],
    ...overrides,
  }
}

const meal = (recipe_id: number, recipe_name: string) => ({
  planned_meal_id: recipe_id * 10,
  date: '2026-09-29',
  slot_id: 1,
  slot_name: 'dinner',
  recipe_id,
  recipe_name,
  servings: 2,
})

const preview: ShoppingPreview = {
  start_date: '2026-09-28',
  end_date: '2026-10-04',
  today: '2026-09-28',
  items: [
    previewItem({
      ingredient_id: 1,
      ingredient_name: 'Milk',
      category: 'Dairy',
      dimension: 'volume',
      base_unit: 'ml',
      required_base: '1500.000',
      stock_base: '700.000',
      reserved_base: '200.000',
      available_base: '500.000',
      to_buy_base: '1000.000',
      display: { amount: '1.000', unit: 'l' },
      nothing_to_buy: false,
      unconverted: [
        {
          amount: '2.000',
          unit: 'piece',
          reason: 'missing_grams_per_piece',
          recipe_names: ['Pancakes'],
          meals: [meal(5, 'Pancakes')],
        },
      ],
    }),
    previewItem({
      ingredient_id: 2,
      ingredient_name: 'Butter',
      category: 'Dairy',
      required_base: '50.000',
      stock_base: '250.000',
      available_base: '250.000',
      nothing_to_buy: true,
    }),
    previewItem({
      ingredient_id: 3,
      ingredient_name: 'Flour',
      category: 'Pantry',
      required_base: '600.000',
      available_base: '100.000',
      to_buy_base: '500.000',
      display: { amount: '500.000', unit: 'g' },
      nothing_to_buy: false,
    }),
  ],
  check_have: [
    {
      ingredient_id: 4,
      ingredient_name: 'Salt',
      category: 'Pantry',
      recipe_names: ['Pancakes', 'Soup'],
      meals: [meal(5, 'Pancakes'), meal(6, 'Soup')],
    },
  ],
}

function listItem(overrides: Partial<ShoppingListItem> & Pick<ShoppingListItem, 'id' | 'ingredient_id' | 'ingredient_name'>): ShoppingListItem {
  return {
    list_id: 7,
    position: overrides.id,
    kind: 'buy',
    category: null,
    base_unit: 'g',
    required_base: '0.000',
    available_base: '0.000',
    to_buy_base: '0.000',
    display: { amount: '0.000', unit: 'g' },
    checked: false,
    bought_base: null,
    bought_display: null,
    unconverted: [],
    recipe_names: [],
    ...overrides,
  }
}

const milkItem = listItem({
  id: 71,
  ingredient_id: 1,
  ingredient_name: 'Milk',
  category: 'Dairy',
  base_unit: 'ml',
  to_buy_base: '1000.000',
  display: { amount: '1.000', unit: 'l' },
})
const flourItem = listItem({
  id: 72,
  ingredient_id: 3,
  ingredient_name: 'Flour',
  category: 'Pantry',
  to_buy_base: '500.000',
  display: { amount: '500.000', unit: 'g' },
})
const saltItem = listItem({
  id: 73,
  ingredient_id: 4,
  ingredient_name: 'Salt',
  kind: 'check_have',
  category: 'Pantry',
  recipe_names: ['Soup'],
})

function shoppingList(overrides: Partial<ShoppingList> = {}): ShoppingList {
  const items = overrides.items ?? [milkItem, flourItem, saltItem]
  return {
    id: 7,
    start_date: '2026-09-28',
    end_date: '2026-10-04',
    status: 'open',
    created_by: 1,
    created_at: '2026-09-28T09:00:00Z',
    completed_at: null,
    item_count: items.length,
    checked_count: items.filter((i) => i.checked).length,
    items,
    ...overrides,
  }
}

function summary(list: ShoppingList): ShoppingListSummary {
  const { items, ...rest } = list
  void items
  return rest
}

function ingredient(id: number, name: string, extra: Partial<Ingredient> = {}): Ingredient {
  return {
    id,
    name,
    dimension: 'mass',
    default_unit: 'g',
    category: null,
    grams_per_ml: null,
    grams_per_piece: null,
    stock: { quantity_base: '0.000', base_unit: 'g', display: { amount: '0.000', unit: 'g' } },
    created_at: '2026-09-01T10:00:00Z',
    updated_at: '2026-09-01T10:00:00Z',
    ...extra,
  }
}

function serveList(list: ShoppingList) {
  server.use(
    http.get(`${API}/shopping-lists/${list.id}`, () => HttpResponse.json(list)),
    http.get(`${API}/shopping-lists`, () => HttpResponse.json([summary(list)])),
    http.get(`${API}/units`, () => HttpResponse.json(units)),
    http.get(`${API}/ingredients`, () =>
      HttpResponse.json([
        ingredient(1, 'Milk', { dimension: 'volume', default_unit: 'ml', grams_per_ml: '1.030' }),
        ingredient(3, 'Flour'),
        ingredient(4, 'Salt'),
      ]),
    ),
  )
}

const checkbox = (name: string) => screen.getByRole('checkbox', { name: new RegExp(`^${name}`) })

beforeEach(() => server.use(meAs(alice)))

describe('shopping planner', () => {
  it('previews the range grouped by category with to-taste and unconverted sections', async () => {
    const bodies: unknown[] = []
    server.use(
      http.post(`${API}/shopping/preview`, async ({ request }) => {
        bodies.push(await request.json())
        return HttpResponse.json(preview)
      }),
      http.get(`${API}/shopping-lists`, () => HttpResponse.json([])),
    )
    renderApp('/shopping?from=2026-09-28&to=2026-10-04')
    const user = userEvent.setup()

    const dairy = await screen.findByRole('region', { name: 'Dairy' })
    expect(bodies[0]).toEqual({ from: '2026-09-28', to: '2026-10-04' })
    expect(within(dairy).getByText('Milk')).toBeInTheDocument()
    expect(within(dairy).getByText('1 l')).toBeInTheDocument()
    expect(
      within(dairy).getByText('Need 1.5 l · In stock 500 ml (200 ml held for earlier meals)'),
    ).toBeInTheDocument()
    expect(within(dairy).getByText('In stock')).toBeInTheDocument()
    const pantry = screen.getByRole('region', { name: 'Pantry' })
    expect(within(pantry).getByText('500 g')).toBeInTheDocument()
    expect(screen.getByText('2 items to buy')).toBeInTheDocument()

    const toTaste = screen.getByText('Check you have (1)')
    await user.click(toTaste)
    expect(within(screen.getByRole('list', { name: 'Check you have' })).getByText('Pancakes, Soup')).toBeVisible()

    const unconverted = screen.getByRole('region', { name: "Couldn't convert" })
    expect(within(unconverted).getByText(/2 pieces · needs grams per piece/)).toBeInTheDocument()
    expect(within(unconverted).getByRole('link', { name: 'Pancakes' })).toHaveAttribute(
      'href',
      '/recipes/5',
    )

    await user.click(screen.getByRole('radio', { name: 'Next week' }))
    const nextWeek = PRESETS.find((p) => p.id === 'next-week')!.range(new Date())
    await waitFor(() => expect(bodies.at(-1)).toEqual(nextWeek))
    expect(screen.getByRole('radio', { name: 'Next week' })).toHaveAttribute('aria-checked', 'true')
  })

  it('flags a backwards custom range without calling the API', async () => {
    let calls = 0
    server.use(
      http.post(`${API}/shopping/preview`, () => {
        calls++
        return HttpResponse.json(preview)
      }),
      http.get(`${API}/shopping-lists`, () => HttpResponse.json([])),
    )
    renderApp('/shopping?from=2026-10-05&to=2026-10-01')
    expect(await screen.findByRole('alert')).toHaveTextContent('The end date is before the start date')
    expect(calls).toBe(0)
  })

  it('saves a snapshot and opens it', async () => {
    let body: unknown
    const list = shoppingList()
    serveList(list)
    server.use(
      http.post(`${API}/shopping/preview`, () => HttpResponse.json(preview)),
      http.post(`${API}/shopping-lists`, async ({ request }) => {
        body = await request.json()
        return HttpResponse.json(list, { status: 201 })
      }),
    )
    const { router } = renderApp('/shopping?from=2026-09-28&to=2026-10-04')
    const user = userEvent.setup()

    await screen.findByRole('region', { name: 'Dairy' })
    await user.click(screen.getByRole('button', { name: 'Save list' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/shopping/lists/7'))
    expect(body).toEqual({ from: '2026-09-28', to: '2026-10-04' })
    expect(await screen.findByText('List saved')).toBeInTheDocument()
    expect(checkbox('Milk')).toHaveAttribute('aria-checked', 'false')
    expect(within(screen.getByRole('region', { name: 'Check you have' })).getByText('Salt')).toBeInTheDocument()
  })

  it('lists saved lists with status and counts', async () => {
    server.use(
      http.post(`${API}/shopping/preview`, () => HttpResponse.json(preview)),
      http.get(`${API}/shopping-lists`, () =>
        HttpResponse.json([
          summary(shoppingList({ id: 9, status: 'done', checked_count: 3, item_count: 3 })),
        ]),
      ),
    )
    renderApp('/shopping')
    const link = await screen.findByRole('link', { name: /3\/3 checked/ })
    expect(link).toHaveAttribute('href', '/shopping/lists/9')
    expect(within(link).getByText('Done')).toBeInTheDocument()
  })
})

describe('shopping checklist', () => {
  it('toggles an item optimistically and rolls back when the server rejects it', async () => {
    serveList(shoppingList())
    let body: unknown
    let release!: () => void
    const gate = new Promise<void>((resolve) => (release = resolve))
    server.use(
      http.patch(`${API}/shopping-lists/7/items/72`, async ({ request }) => {
        body = await request.json()
        await gate
        return problem(409, 'Conflict', 'shopping list 7 is already completed')
      }),
    )
    renderApp('/shopping/lists/7')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('checkbox', { name: /^Flour/ }))
    expect(checkbox('Flour')).toHaveAttribute('aria-checked', 'true')
    expect(within(checkbox('Flour')).getByRole('status', { name: 'Saving' })).toBeInTheDocument()

    release()
    expect(await screen.findByText("Couldn't update Flour")).toBeInTheDocument()
    expect(screen.getByText('shopping list 7 is already completed')).toBeInTheDocument()
    expect(checkbox('Flour')).toHaveAttribute('aria-checked', 'false')
    expect(body).toEqual({ checked: true })
  })

  it('keeps a failed toggle pending and retries it', async () => {
    serveList(shoppingList())
    let attempts = 0
    server.use(
      http.patch(`${API}/shopping-lists/7/items/71`, () => {
        attempts++
        if (attempts === 1) return HttpResponse.error()
        return HttpResponse.json({ ...milkItem, checked: true })
      }),
    )
    renderApp('/shopping/lists/7')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('checkbox', { name: /^Milk/ }))
    expect(
      await within(checkbox('Milk')).findByRole('status', { name: 'Not saved yet, retrying' }),
    ).toBeInTheDocument()
    expect(checkbox('Milk')).toHaveAttribute('aria-checked', 'true')

    await waitFor(() => expect(within(checkbox('Milk')).queryByRole('status')).toBeNull(), {
      timeout: 3000,
    })
    expect(attempts).toBe(2)
    expect(checkbox('Milk')).toHaveAttribute('aria-checked', 'true')
  })

  it('sets the bought quantity in another convertible unit', async () => {
    serveList(shoppingList())
    let body: unknown
    server.use(
      http.patch(`${API}/shopping-lists/7/items/71`, async ({ request }) => {
        body = await request.json()
        return HttpResponse.json({
          ...milkItem,
          checked: true,
          bought_base: '480.000',
          bought_display: { amount: '480.000', unit: 'ml' },
        })
      }),
    )
    renderApp('/shopping/lists/7')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Edit bought amount for Milk' }))
    const drawer = await screen.findByRole('dialog')
    const unit = within(drawer).getByRole('combobox', { name: 'Unit' })
    await waitFor(() =>
      expect(within(unit).getAllByRole('option').map((o) => o.textContent)).toEqual([
        'g',
        'kg',
        'ml',
        'l',
        'cup',
      ]),
    )
    expect(within(drawer).getByLabelText('Bought')).toHaveValue('1')

    await user.clear(within(drawer).getByLabelText('Bought'))
    await user.type(within(drawer).getByLabelText('Bought'), '2')
    await user.selectOptions(unit, 'cup')
    await user.click(within(drawer).getByRole('button', { name: 'Save bought amount' }))

    await waitFor(() => expect(body).toEqual({ checked: true, bought_quantity: '2', unit: 'cup' }))
    expect(await screen.findByText('Bought 480 ml')).toBeInTheDocument()
    expect(checkbox('Milk')).toHaveAttribute('aria-checked', 'true')
  })

  it('completes with a summary, then invalidates stock and shows the count', async () => {
    const checkedMilk = {
      ...milkItem,
      checked: true,
      bought_base: '2000.000',
      bought_display: { amount: '2.000', unit: 'l' },
    }
    const checkedFlour = { ...flourItem, checked: true }
    const open = shoppingList({ items: [checkedMilk, checkedFlour, saltItem] })
    serveList(open)
    let completed = false
    server.use(
      http.post(`${API}/shopping-lists/7/complete`, () => {
        completed = true
        return HttpResponse.json({
          ...open,
          status: 'done',
          completed_at: '2026-09-28T12:00:00Z',
        })
      }),
    )
    const { queryClient } = renderApp('/shopping/lists/7')
    queryClient.setQueryData(stockKeys.list(), [])
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Complete shopping' }))
    const dialog = await screen.findByRole('alertdialog')
    const adding = within(dialog).getByRole('list', { name: 'Adding to stock' })
    expect(within(adding).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Milk+2 l',
      'Flour+500 g',
    ])
    expect(within(dialog).getByText('1 unchecked item is not added.')).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: 'Complete' }))
    expect(await screen.findByText('Added 2 items to stock')).toBeInTheDocument()
    expect(completed).toBe(true)
    expect(queryClient.getQueryState(stockKeys.list())?.isInvalidated).toBe(true)
    expect(screen.getByText('Done')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Complete shopping' })).toBeNull()
  })

  it('shows a done list read-only', async () => {
    let patched = false
    serveList(
      shoppingList({
        status: 'done',
        items: [{ ...milkItem, checked: true, bought_display: { amount: '2.000', unit: 'l' } }],
      }),
    )
    server.use(
      http.patch(`${API}/shopping-lists/7/items/71`, () => {
        patched = true
        return HttpResponse.json(milkItem)
      }),
    )
    renderApp('/shopping/lists/7')
    const user = userEvent.setup()

    const milk = await screen.findByRole('checkbox', { name: /^Milk/ })
    expect(milk).toBeDisabled()
    await user.click(milk)
    expect(milk).toHaveAttribute('aria-checked', 'true')
    expect(patched).toBe(false)
    expect(screen.getByText(/read-only/)).toBeInTheDocument()
    expect(screen.getByText('Bought 2 l')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Edit bought amount/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Complete shopping' })).toBeNull()
  })

  it('deletes a list after confirming', async () => {
    serveList(shoppingList())
    let deleted = false
    server.use(
      http.delete(`${API}/shopping-lists/7`, () => {
        deleted = true
        return new HttpResponse(null, { status: 204 })
      }),
      http.post(`${API}/shopping/preview`, () => HttpResponse.json(preview)),
    )
    const { router } = renderApp('/shopping/lists/7')
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: 'Delete' }))
    const dialog = await screen.findByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/shopping'))
    expect(deleted).toBe(true)
    expect(await screen.findByText('List deleted')).toBeInTheDocument()
  })
})
