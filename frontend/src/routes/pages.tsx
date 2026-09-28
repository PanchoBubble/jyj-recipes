import { lazy, Suspense, useEffect, type ComponentType, type ReactNode } from 'react'

import { Skeleton } from '@/components/ui/skeleton'
import { installRoutePrefetch, pageLoaders } from '@/routes/prefetch'

export function PageSkeleton() {
  return (
    <div role="status" aria-label="Loading" className="space-y-3">
      <Skeleton className="h-8 w-1/2" />
      <Skeleton className="h-20 w-full" />
      <Skeleton className="h-20 w-full" />
      <Skeleton className="h-20 w-full" />
    </div>
  )
}

function lazyPage<M, K extends keyof M>(loader: () => Promise<M>, name: K) {
  return lazy(async () => ({ default: (await loader())[name] as ComponentType }))
}

function Suspended({ page: Page }: { page: ComponentType }) {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <Page />
    </Suspense>
  )
}

const LazyCalendarPage = lazyPage(pageLoaders.calendar, 'CalendarPage')
const LazyRecipesPage = lazyPage(pageLoaders.recipes, 'RecipesPage')
const LazyRecipeDetailPage = lazyPage(pageLoaders.recipeDetail, 'RecipeDetailPage')
const LazyRecipeEditorPage = lazyPage(pageLoaders.recipeEditor, 'RecipeEditorPage')
const LazyShoppingPage = lazyPage(pageLoaders.shopping, 'ShoppingPage')
const LazyShoppingListPage = lazyPage(pageLoaders.shoppingList, 'ShoppingListPage')
const LazyStockPage = lazyPage(pageLoaders.stock, 'StockPage')
const LazySettingsPage = lazyPage(pageLoaders.settings, 'SettingsPage')

export const CalendarPage = () => <Suspended page={LazyCalendarPage} />
export const RecipesPage = () => <Suspended page={LazyRecipesPage} />
export const RecipeDetailPage = () => <Suspended page={LazyRecipeDetailPage} />
export const RecipeEditorPage = () => <Suspended page={LazyRecipeEditorPage} />
export const ShoppingPage = () => <Suspended page={LazyShoppingPage} />
export const ShoppingListPage = () => <Suspended page={LazyShoppingListPage} />
export const StockPage = () => <Suspended page={LazyStockPage} />
export const SettingsPage = () => <Suspended page={LazySettingsPage} />

export function RoutePrefetch({ children }: { children: ReactNode }) {
  useEffect(installRoutePrefetch, [])
  return children
}
