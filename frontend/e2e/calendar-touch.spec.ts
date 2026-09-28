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
async function touchGesture(
  page: Page,
  from: Point,
  to: Point,
  { holdMs = 0, steps = 10, stepMs = 16 } = {},
) {
  const cdp = await page.context().newCDPSession(page)
  const touch = (type: string, points: Point[]) =>
    cdp.send('Input.dispatchTouchEvent', {
      type: type as 'touchStart',
      touchPoints: points.map((p) => ({ x: Math.round(p.x), y: Math.round(p.y) })),
    })
  await touch('touchStart', [from])
  if (holdMs) await page.waitForTimeout(holdMs)
  for (let i = 1; i <= steps; i++) {
    await touch('touchMove', [
      { x: from.x + ((to.x - from.x) * i) / steps, y: from.y + ((to.y - from.y) * i) / steps },
    ])
    await page.waitForTimeout(stepMs)
  }
  await touch('touchEnd', [])
  await cdp.detach()
}

/** Waits out any fling so the next gesture starts from a still page. */
async function settle(page: Page) {
  let last = -1
  for (let i = 0; i < 40; i++) {
    const y = await page.evaluate(() => window.scrollY)
    if (y === last) return
    last = y
    await page.waitForTimeout(100)
  }
  throw new Error('page kept scrolling')
}

const cell = (page: Page, date: string, slotId: number) =>
  page.locator(`[data-cell="cell:${date}:${slotId}"]`)

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
  const handle = page.getByRole('button', { name: 'Move Lentil soup' })
  const from = await center(handle)
  await touchGesture(page, from, { x: from.x, y: from.y - 150 })
  await settle(page)
  expect(await scrollY()).toBe(0)
  await expect(cell(page, WEEK, 1).getByRole('button', { name: /^Lentil soup/ })).toBeVisible()

  expect(calls).toEqual([])
})
