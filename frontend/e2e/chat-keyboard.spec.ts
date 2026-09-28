import { expect, test, type Locator, type Page } from '@playwright/test'

import { mockApi, WEEK } from './fake-api.ts'

/** Height left above a typical phone keyboard. */
const KEYBOARD = 330

async function openChat(page: Page) {
  await mockApi(page)
  await page.goto(`/calendar?week=${WEEK}`)
  await page.getByRole('button', { name: 'Open chat' }).click()
  const panel = page.getByRole('dialog', { name: 'Assistant' })
  await expect(panel).toBeVisible()
  return panel
}

/** Waits for the element to lie fully inside the visible (visual) viewport. */
async function expectVisible(page: Page, locator: Locator) {
  await expect(async () => {
    const box = await locator.boundingBox()
    const visible = await page.evaluate(() => ({
      top: window.visualViewport?.offsetTop ?? 0,
      height: window.visualViewport?.height ?? window.innerHeight,
    }))
    expect(box).not.toBeNull()
    expect(box!.y).toBeGreaterThanOrEqual(visible.top - 1)
    expect(box!.y + box!.height).toBeLessThanOrEqual(visible.top + visible.height + 1)
  }).toPass({ timeout: 3000 })
}

test('Android: the sheet shrinks with the resized viewport and keeps the composer in view', async ({
  page,
}) => {
  const panel = await openChat(page)
  const composer = panel.getByRole('textbox', { name: 'Message' })
  await composer.focus()

  // interactive-widget=resizes-content: the keyboard shrinks the layout viewport itself.
  const { width, height } = page.viewportSize()!
  await page.setViewportSize({ width, height: height - KEYBOARD })

  await expectVisible(page, composer)
  await expectVisible(page, panel.getByRole('heading', { name: 'Assistant' }))
  await expectVisible(page, panel.getByRole('button', { name: 'Send' }))
  await expect(composer).toBeFocused()
  expect(await page.evaluate(() => window.scrollY)).toBe(0)
})

test('iOS: the sheet rises above a keyboard that only shrinks the visual viewport', async ({ page }) => {
  // Safari overlays the keyboard: innerHeight stays, visualViewport shrinks and may scroll.
  await page.addInitScript(() => {
    const fake = Object.assign(new EventTarget(), { height: window.innerHeight, offsetTop: 0 })
    Object.defineProperty(window, 'visualViewport', { configurable: true, value: fake })
    ;(window as unknown as { keyboard: (h: number, top?: number) => void }).keyboard = (h, top = 0) => {
      fake.height = window.innerHeight - h - top
      fake.offsetTop = top
      fake.dispatchEvent(new Event('resize'))
    }
  })
  const panel = await openChat(page)
  const composer = panel.getByRole('textbox', { name: 'Message' })
  await composer.focus()
  await page.evaluate((h) => (window as unknown as { keyboard: (h: number) => void }).keyboard(h), KEYBOARD)

  await expect
    .poll(() => panel.evaluate((el) => getComputedStyle(el).getPropertyValue('--keyboard-inset')))
    .toBe(`${KEYBOARD}px`)
  await expectVisible(page, composer)
  await expectVisible(page, panel.getByRole('heading', { name: 'Assistant' }))

  // The layout viewport scrolled on focus: the sheet follows the visual viewport, not the page.
  await page.evaluate((h) => (window as unknown as { keyboard: (h: number, t: number) => void }).keyboard(h, 60), KEYBOARD)
  await expect
    .poll(() => panel.evaluate((el) => getComputedStyle(el).getPropertyValue('--keyboard-inset')))
    .toBe(`${KEYBOARD}px`)
  await expectVisible(page, composer)

  await page.evaluate(() => (window as unknown as { keyboard: (h: number) => void }).keyboard(0))
  await expect
    .poll(() => panel.evaluate((el) => getComputedStyle(el).getPropertyValue('--keyboard-inset')))
    .toBe('0px')
})

test('the calendar keeps its recipe search visible when the keyboard resizes the viewport', async ({
  page,
}) => {
  await mockApi(page)
  await page.goto(`/calendar?week=${WEEK}`)
  const search = page.getByRole('searchbox', { name: 'Search recipes' })
  await search.focus()

  const { width, height } = page.viewportSize()!
  await page.setViewportSize({ width, height: height - KEYBOARD })

  await expectVisible(page, search)
  const week = await page.getByTestId('week').boundingBox()
  expect(week!.height).toBeGreaterThan(0)
  const size = await page.evaluate(() => ({
    width: document.documentElement.scrollWidth,
    height: document.documentElement.scrollHeight,
  }))
  expect(size).toEqual({ width, height: height - KEYBOARD })
})
