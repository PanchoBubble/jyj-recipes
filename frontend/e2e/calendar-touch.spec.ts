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

/** Waits out any fling so the next gesture starts from a still page. */
async function settle(page: Page) {
  let last = -1
  let still = 0
  for (let i = 0; i < 60; i++) {
    const y = await page.evaluate(() => window.scrollY)
    still = y === last ? still + 1 : 0
    if (still >= 5) return
    last = y
    await page.waitForTimeout(100)
  }
  throw new Error('page kept scrolling')
}

const cell = (page: Page, date: string, slotId: number) =>
  page.locator(`[data-cell="cell:${date}:${slotId}"]`)

const SUNDAY = '2026-10-04'

const grid = (page: Page) => page.getByTestId('week-grid')
const gridScrollLeft = (page: Page) => grid(page).evaluate((el) => el.scrollLeft)

async function inViewport(page: Page, locator: Locator) {
  const box = await locator.boundingBox()
  const { width } = page.viewportSize()!
  // Column widths are fractional, so allow a pixel of rounding.
  return !!box && box.x >= -1 && box.x + box.width <= width + 1
}

test('long-press drag from the recipe tray plans a meal in the drop cell', async ({ page }) => {
  const calls = await mockApi(page)
  await page.goto(`/calendar?week=${WEEK}`)

  const target = cell(page, WEEK, 2)
  await expect(target).toBeVisible()
  await page.getByRole('button', { name: 'Recipes' }).tap()
  const handle = page.getByRole('button', { name: 'Drag Pancakes onto the calendar' })
  await expect(handle).toBeVisible()

  await touchGesture(page, await center(handle), await center(target), { holdMs: 350, steps: 20 })

  await expect(target.getByRole('button', { name: /^Pancakes, 2 servings/ })).toBeVisible()
  expect(calls).toEqual([
    {
      method: 'POST',
      path: '/planned-meals',
      body: { date: WEEK, slot_id: 2, recipe_id: 7, servings: 2 },
    },
  ])
})

test('shows about three day columns at a time with today or Monday first', async ({ page }) => {
  await mockApi(page)
  await page.goto(`/calendar?week=${WEEK}`)
  await expect(cell(page, WEEK, 1)).toBeVisible()

  expect(await inViewport(page, cell(page, WEEK, 1))).toBe(true)
  expect(await inViewport(page, cell(page, '2026-09-30', 1))).toBe(true)
  expect(await inViewport(page, cell(page, '2026-10-01', 1))).toBe(false)
  expect(await inViewport(page, cell(page, SUNDAY, 1))).toBe(false)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    page.viewportSize()!.width,
  )
})

test('dragging a recipe to the edge auto-scrolls to an off-screen day and plans it there', async ({
  page,
}) => {
  const calls = await mockApi(page)
  await page.goto(`/calendar?week=${WEEK}`)

  const target = cell(page, SUNDAY, 2)
  await expect(cell(page, WEEK, 2)).toBeVisible()
  expect(await inViewport(page, target)).toBe(false)
  await page.getByRole('button', { name: 'Recipes' }).tap()
  const handle = page.getByRole('button', { name: 'Drag Pancakes onto the calendar' })
  await expect(handle).toBeVisible()

  const { width } = page.viewportSize()!
  const row = await center(cell(page, WEEK, 2))
  const f = await finger(page, await center(handle))
  await page.waitForTimeout(350)
  await f.moveTo({ x: width - 8, y: row.y }, { steps: 20 })
  await expect.poll(() => inViewport(page, target), { timeout: 5000 }).toBe(true)
  await f.moveTo(await center(target), { steps: 5 })
  await f.lift()

  await expect(target.getByRole('button', { name: /^Pancakes, 2 servings/ })).toBeVisible()
  expect(calls).toEqual([
    {
      method: 'POST',
      path: '/planned-meals',
      body: { date: SUNDAY, slot_id: 2, recipe_id: 7, servings: 2 },
    },
  ])
})

test('a horizontal swipe outside drag handles scrolls the day columns', async ({ page }) => {
  const calls = await mockApi(page)
  await page.goto(`/calendar?week=${WEEK}`)
  const card = cell(page, WEEK, 1).getByRole('button', { name: /^Lentil soup/ })
  await expect(card).toBeVisible()
  expect(await gridScrollLeft(page)).toBe(0)

  const start = await center(card)
  await touchGesture(page, { x: start.x + 150, y: start.y }, { x: start.x - 50, y: start.y })
  await expect.poll(() => gridScrollLeft(page)).toBeGreaterThan(50)
  await expect.poll(() => inViewport(page, cell(page, '2026-10-01', 1))).toBe(true)

  // The day strip follows the columns, and the week stays put.
  const strip = page.getByTestId('day-strip')
  await expect
    .poll(async () => (await strip.evaluate((el) => el.scrollLeft)) - (await gridScrollLeft(page)))
    .toBe(0)
  expect(new URL(page.url()).searchParams.get('week')).toBe(WEEK)
  expect(await page.evaluate(() => window.scrollY)).toBe(0)
  expect(calls).toEqual([])
})

test('swiping outside drag handles scrolls the page and never drags', async ({ page }) => {
  const calls = await mockApi(page)
  await page.goto(`/calendar?week=${WEEK}`)
  await expect(cell(page, WEEK, 1).getByRole('button', { name: /^Lentil soup/ })).toBeVisible()

  const viewport = page.viewportSize()!
  const scrollY = () => page.evaluate(() => window.scrollY)
  expect(await scrollY()).toBe(0)

  // Start on a meal card body, which is also a drop target and sits next to its handle.
  const card = cell(page, WEEK, 1).getByRole('button', { name: /^Lentil soup/ })
  const start = await center(card)
  await touchGesture(page, start, { x: start.x, y: start.y - viewport.height * 0.4 })
  await expect.poll(scrollY).toBeGreaterThan(100)

  // A quick swipe that starts on a handle moves past the tolerance before the hold delay,
  // so it neither scrolls (touch-action: none) nor starts a drag.
  await settle(page)
  await page.evaluate(() => window.scrollTo(0, 0))
  await settle(page)
  expect(await scrollY()).toBe(0)
  const handle = cell(page, WEEK, 1).getByRole('button', { name: 'Move Lentil soup' })
  const from = await center(handle)
  await touchGesture(page, from, { x: from.x, y: from.y - 150 })
  await settle(page)
  expect(await scrollY()).toBe(0)
  await expect(cell(page, WEEK, 1).getByRole('button', { name: /^Lentil soup/ })).toBeVisible()

  expect(calls).toEqual([])
})
