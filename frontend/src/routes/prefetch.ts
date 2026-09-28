type Loader = () => Promise<unknown>

export const pageLoaders = {
  calendar: () => import('@/features/calendar/CalendarPage'),
  recipes: () => import('@/features/recipes/RecipesPage'),
  recipeDetail: () => import('@/features/recipes/RecipeDetailPage'),
  recipeEditor: () => import('@/features/recipes/RecipeEditorPage'),
  shopping: () => import('@/features/shopping/ShoppingPage'),
  shoppingList: () => import('@/features/shopping/ShoppingListPage'),
  stock: () => import('@/features/stock/StockPage'),
  settings: () => import('@/routes/SettingsPage'),
}

const requested = new Set<Loader>()

export function prefetch(loader: Loader) {
  if (requested.has(loader)) return
  requested.add(loader)
  // A failed prefetch (offline, new deploy) is retried by the next hover or the real navigation.
  loader().catch(() => requested.delete(loader))
}

const loadersByPath: [RegExp, Loader][] = [
  [/^\/calendar\/?$/, pageLoaders.calendar],
  [/^\/recipes\/?$/, pageLoaders.recipes],
  [/^\/recipes\/new\/?$/, pageLoaders.recipeEditor],
  [/^\/recipes\/[^/]+\/edit\/?$/, pageLoaders.recipeEditor],
  [/^\/recipes\/[^/]+\/?$/, pageLoaders.recipeDetail],
  [/^\/shopping\/?$/, pageLoaders.shopping],
  [/^\/shopping\/lists\/[^/]+\/?$/, pageLoaders.shoppingList],
  [/^\/stock\/?$/, pageLoaders.stock],
  [/^\/settings\/?$/, pageLoaders.settings],
]

export function loaderFor(pathname: string): Loader | undefined {
  return loadersByPath.find(([pattern]) => pattern.test(pathname))?.[1]
}

const tabLoaders = [
  pageLoaders.calendar,
  pageLoaders.recipes,
  pageLoaders.shopping,
  pageLoaders.stock,
]

function onIdle(callback: () => void) {
  if (typeof window.requestIdleCallback === 'function') {
    const id = window.requestIdleCallback(callback, { timeout: 5000 })
    return () => window.cancelIdleCallback(id)
  }
  const id = window.setTimeout(callback, 2000)
  return () => window.clearTimeout(id)
}

function prefetchLinkTarget(event: Event) {
  if (!(event.target instanceof Element)) return
  const link = event.target.closest('a[href]')
  if (!(link instanceof HTMLAnchorElement) || link.origin !== window.location.origin) return
  const loader = loaderFor(link.pathname)
  if (loader) prefetch(loader)
}

const intentEvents = ['pointerover', 'touchstart', 'focusin'] as const

/**
 * Warms route chunks so tab switches on a phone don't wait on the network: an in-app link
 * the pointer rests on, a finger lands on or focus reaches starts its chunk, and once the
 * first screen is idle the bottom-nav tabs load in the background.
 */
export function installRoutePrefetch() {
  const options = { capture: true, passive: true }
  intentEvents.forEach((type) => document.addEventListener(type, prefetchLinkTarget, options))
  const cancelIdle = onIdle(() => tabLoaders.forEach(prefetch))
  return () => {
    intentEvents.forEach((type) => document.removeEventListener(type, prefetchLinkTarget, options))
    cancelIdle()
  }
}
