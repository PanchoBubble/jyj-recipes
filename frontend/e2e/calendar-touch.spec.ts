import { expect, test, type Locator, type Page } from '@playwright/test'

import { mockApi, WEEK } from './fake-api.ts'

type Point = { x: number; y: number }

async function center(locator: Locator): Promise<Point> {
  const box = await locator.boundingBox()
  if (!box) throw new Error('element is not visible')
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 }
}

/**
 * A real touch sequence through Chromium's input pipeline (not synthetic DOM events), so
 * touch-action, native scrolling and dnd-kit's TouchSensor all see what a finger would do.
 */
async function finger(page: Page, from: Point) {
  const cdp = await page.context().newCDPSession(page)
  const touch = (type: string, points: Point[]) =>
    cdp.send('Input.dispatchTouchEvent', {
      type: type as 'touchStart',
      touchPoints: points.map((p) => ({ x: Math.round(p.x), y: Math.round(p.y) })),
    })
  let at = from
  await touch('touchStart', [from])
  return {
    async moveTo(to: Point, { steps = 10, stepMs = 16 } = {}) {
      const start = at
      for (let i = 1; i <= steps; i++) {
        at = { x: start.x + ((to.x - start.x) * i) / steps, y: start.y + ((to.y - start.y) * i) / steps }
        await touch('touchMove', [at])
        await page.waitForTimeout(stepMs)
      }
    },
    async lift() {
      await touch('touchEnd', [])
      await cdp.detach()
    },
  }
}

async function touchGesture(
  page: Page,
  from: Point,
  to: Point,
  { holdMs = 0, steps = 10, stepMs = 16 } = {},
) {
  const f = await finger(page, from)
  if (holdMs) await page.waitForTimeout(holdMs)
  await f.moveTo(to, { steps, stepMs })
  await f.lift()
}

/** Waits out any fling so the next gesture starts from a still scroller. */
async function settle(locator: Locator) {
  let last = ''
  let still = 0
  for (let i = 0; i < 60; i++) {
    const at = await locator.evaluate((el) => `${el.scrollLeft},${el.scrollTop}`)
    still = at === last ? still + 1 : 0
    if (still >= 5) return
    last = at
    await locator.page().waitForTimeout(100)
  }
  throw new Error('kept scrolling')
}

const day = (page: Page, date: string) => page.locator(`[data-day-column="${date}"]`)
const card = (page: Page, date: string, name: string) =>
  day(page, date).getByRole('button', { name: new RegExp(`^${name}, `) })
const recipeHandle = (page: Page, name: string) =>
  page.getByRole('button', { name: `Drag ${name} onto the calendar` })

const TUESDAY = '2026-09-29'
const SUNDAY = '2026-10-04'

const grid = (page: Page) => page.getByTestId('week-grid')
const recipeList = (page: Page) => page.getByTestId('recipe-list')
const scrollOf = (locator: Locator) =>
  locator.evaluate((el) => ({ left: el.scrollLeft, top: el.scrollTop }))
const pageScroll = (page: Page) => page.evaluate(() => window.scrollY)

async function inViewport(page: Page, locator: Locator) {
  const box = await locator.boundingBox()
  const { width } = page.viewportSize()!
  // Column widths are fractional, so allow a pixel of rounding.
  return !!box && box.x >= -1 && box.x + box.width <= width + 1
}

/** Near the top of a day column: a long week makes columns taller than the visible grid. */
async function dropPoint(locator: Locator): Promise<Point> {
  const box = await locator.boundingBox()
  if (!box) throw new Error('element is not visible')
  return { x: box.x + box.width / 2, y: box.y + 40 }
}

async function mondayOrder(page: Page) {
  return day(page, WEEK)
    .getByRole('button', { name: /servings/ })
    .evaluateAll((els) => els.map((el) => el.getAttribute('aria-label')!.split(',')[0]))
}

test('long-press drag from the docked recipe panel plans a meal in the drop day', async ({ page }) => {
  const calls = await mockApi(page)
  await page.goto(`/calendar?week=${WEEK}`)

  const target = day(page, TUESDAY)
  await expect(target).toBeVisible()
  const handle = recipeHandle(page, 'Pancakes')
  await expect(handle).toBeVisible()

  await touchGesture(page, await center(handle), await dropPoint(target), { holdMs: 350, steps: 20 })

  await expect(card(page, TUESDAY, 'Pancakes')).toBeVisible()
  expect(calls).toEqual([
    {
      method: 'POST',
      path: '/planned-meals',
      body: { date: TUESDAY, recipe_id: 7, servings: 2, position: 0 },
    },
  ])
})

test('the week and the recipe panel split the screen and the page never scrolls', async ({ page }) => {
  await mockApi(page)
  await page.goto(`/calendar?week=${WEEK}`)
  await expect(card(page, WEEK, 'Lentil soup')).toBeVisible()

  expect(await inViewport(page, day(page, WEEK))).toBe(true)
  expect(await inViewport(page, day(page, TUESDAY))).toBe(true)
  expect(await inViewport(page, day(page, '2026-10-01'))).toBe(false)
  expect(await inViewport(page, day(page, SUNDAY))).toBe(false)

  const viewport = page.viewportSize()!
  const size = await page.evaluate(() => ({
    width: document.documentElement.scrollWidth,
    height: document.documentElement.scrollHeight,
  }))
  expect(size).toEqual({ width: viewport.width, height: viewport.height })

  const week = (await grid(page).boundingBox())!
  const panel = (await page.getByRole('complementary', { name: 'Recipes' }).boundingBox())!
  const nav = (await page.getByRole('navigation', { name: 'Main' }).boundingBox())!
  expect(panel.y).toBeGreaterThanOrEqual(week.y + week.height - 1)
  expect(panel.y + panel.height).toBeLessThanOrEqual(nav.y + 1)
  expect(week.height / (week.height + panel.height)).toBeGreaterThan(0.5)

  // The chat bubble floats above the panel, clear of its cards.
  const bubble = (await page.getByRole('button', { name: /^Open chat/ }).boundingBox())!
  expect(bubble.y + bubble.height).toBeLessThanOrEqual(panel.y)
})

test('dragging a recipe to the edge auto-scrolls to an off-screen day and plans it there', async ({
  page,
}) => {
  const calls = await mockApi(page)
  await page.goto(`/calendar?week=${WEEK}`)

  const target = day(page, SUNDAY)
  await expect(card(page, WEEK, 'Lentil soup')).toBeVisible()
  expect(await inViewport(page, target)).toBe(false)
  const handle = recipeHandle(page, 'Pancakes')
  await expect(handle).toBeVisible()

  const { width } = page.viewportSize()!
  const box = (await grid(page).boundingBox())!
  const f = await finger(page, await center(handle))
  await page.waitForTimeout(350)
  await f.moveTo({ x: width - 8, y: box.y + box.height / 2 }, { steps: 20 })
  await expect.poll(() => inViewport(page, target), { timeout: 5000 }).toBe(true)
  await f.moveTo(await dropPoint(target), { steps: 5 })
  await f.lift()

  await expect(card(page, SUNDAY, 'Pancakes')).toBeVisible()
  expect(calls).toEqual([
    {
      method: 'POST',
      path: '/planned-meals',
      body: { date: SUNDAY, recipe_id: 7, servings: 2, position: 0 },
    },
  ])
})

test('long-press drag on a meal handle reorders it within its day', async ({ page }) => {
  const calls = await mockApi(page)
  await page.goto(`/calendar?week=${WEEK}`)
  await expect(card(page, WEEK, 'Green curry')).toBeVisible()
  expect((await mondayOrder(page)).slice(0, 3)).toEqual(['Lentil soup', 'Green curry', 'Pancakes'])

  const handle = day(page, WEEK).getByRole('button', { name: 'Move Lentil soup' })
  const to = await center(card(page, WEEK, 'Green curry'))
  await touchGesture(page, await center(handle), to, { holdMs: 350, steps: 20 })

  await expect.poll(async () => (await mondayOrder(page)).slice(0, 3)).toEqual([
    'Green curry',
    'Lentil soup',
    'Pancakes',
  ])
  expect(calls).toEqual([{ method: 'PATCH', path: '/planned-meals/1', body: { position: 1 } }])
})

test('a horizontal swipe outside drag handles scrolls the day columns', async ({ page }) => {
  const calls = await mockApi(page)
  await page.goto(`/calendar?week=${WEEK}`)
  const soup = card(page, WEEK, 'Lentil soup')
  await expect(soup).toBeVisible()
  expect((await scrollOf(grid(page))).left).toBe(0)

  const start = await center(soup)
  await touchGesture(page, { x: start.x + 150, y: start.y }, { x: start.x - 50, y: start.y })
  await expect.poll(async () => (await scrollOf(grid(page))).left).toBeGreaterThan(50)
  await expect.poll(() => inViewport(page, day(page, '2026-10-01'))).toBe(true)

  // The day strip follows the columns, and the week stays put.
  const strip = page.getByTestId('day-strip')
  await expect
    .poll(async () => (await scrollOf(strip)).left - (await scrollOf(grid(page))).left)
    .toBe(0)
  expect(new URL(page.url()).searchParams.get('week')).toBe(WEEK)
  expect(await pageScroll(page)).toBe(0)
  expect(calls).toEqual([])
})

test('a vertical swipe scrolls a long day inside the week grid and never drags', async ({ page }) => {
  const calls = await mockApi(page)
  await page.goto(`/calendar?week=${WEEK}`)
  const soup = card(page, WEEK, 'Lentil soup')
  await expect(soup).toBeVisible()
  expect((await scrollOf(grid(page))).top).toBe(0)

  const start = await center(soup)
  await touchGesture(page, { x: start.x, y: start.y + 100 }, { x: start.x, y: start.y - 60 })
  await expect.poll(async () => (await scrollOf(grid(page))).top).toBeGreaterThan(50)
  expect(await pageScroll(page)).toBe(0)

  // A quick swipe that starts on a handle moves past the tolerance before the hold delay,
  // so it neither scrolls (touch-action: none) nor starts a drag.
  await settle(grid(page))
  await grid(page).evaluate((el) => el.scrollTo(0, 0))
  await settle(grid(page))
  const handle = day(page, WEEK).getByRole('button', { name: 'Move Lentil soup' })
  const from = await center(handle)
  await touchGesture(page, from, { x: from.x, y: from.y - 150 })
  await settle(grid(page))
  expect((await scrollOf(grid(page))).top).toBe(0)
  expect(await mondayOrder(page)).toContain('Lentil soup')

  expect(calls).toEqual([])
})

test('the recipe panel scrolls on its own, leaving the week and the page still', async ({ page }) => {
  const calls = await mockApi(page)
  await page.goto(`/calendar?week=${WEEK}`)
  const list = recipeList(page)
  await expect(recipeHandle(page, 'Pancakes')).toBeVisible()
  expect(await list.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true)

  // Start on a card's name, away from its handle.
  const name = list.getByText('Pancakes', { exact: true })
  const start = await center(name)
  await touchGesture(page, start, { x: start.x, y: start.y - 100 })
  await expect.poll(async () => (await scrollOf(list)).top).toBeGreaterThan(30)

  expect(await scrollOf(grid(page))).toEqual({ left: 0, top: 0 })
  expect(await pageScroll(page)).toBe(0)
  expect(calls).toEqual([])
})
