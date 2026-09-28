import { Search, Users, X } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
} from '@/components/ui/drawer'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { recipeErrorMessage, useRecipes } from '@/features/recipes/api'
import { RecipePhoto } from '@/features/recipes/RecipePhoto'
import { useDebouncedValue } from '@/features/recipes/useDebouncedValue'

import { formatLongDay } from './dates'
import type { IsoDate } from './dates'
import type { TrayRecipe } from './plan'

interface RecipePickerProps {
  /** The day to add to. */
  target: IsoDate | null
  onOpenChange: (open: boolean) => void
  onPick: (date: IsoDate, recipe: TrayRecipe) => void
}

/** Tap-to-add: the no-drag way to plan a meal. */
export function RecipePicker({ target, onOpenChange, onPick }: RecipePickerProps) {
  const [shown, setShown] = useState(target)
  if (target && target !== shown) setShown(target)

  return (
    <Drawer open={target !== null} onOpenChange={onOpenChange}>
      <DrawerContent className="data-[vaul-drawer-direction=bottom]:h-[80svh]">
        {shown && (
          <PickerBody
            key={shown}
            date={shown}
            onPick={(recipe) => {
              onPick(shown, recipe)
              onOpenChange(false)
            }}
          />
        )}
      </DrawerContent>
    </Drawer>
  )
}

function PickerBody({
  date,
  onPick,
}: {
  date: IsoDate
  onPick: (recipe: TrayRecipe) => void
}) {
  const [query, setQuery] = useState('')
  const search = useDebouncedValue(query.trim(), 250)
  const recipes = useRecipes(search)
  const items = recipes.data?.pages.flatMap((p) => p.items) ?? []

  return (
    <div className="flex min-h-0 flex-1 flex-col pb-[env(safe-area-inset-bottom)]">
      <DrawerHeader className="text-left group-data-[vaul-drawer-direction=bottom]/drawer-content:text-left">
        <DrawerTitle className="text-lg">Add to {formatLongDay(date)}</DrawerTitle>
        <DrawerDescription>Pick a recipe for this day.</DrawerDescription>
      </DrawerHeader>
      <div className="relative px-4 pb-2">
        <Search
          className="pointer-events-none absolute top-1/2 left-7 size-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
        <Input
          type="search"
          aria-label="Search recipes"
          placeholder="Search recipes"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="h-11 pr-10 pl-9 [&::-webkit-search-cancel-button]:hidden"
        />
        {query && (
          <button
            type="button"
            aria-label="Clear search"
            onClick={() => setQuery('')}
            className="absolute top-0 right-4 inline-flex size-11 items-center justify-center text-muted-foreground"
          >
            <X className="size-4" aria-hidden />
          </button>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
        {recipes.isPending ? (
          <div className="flex flex-col gap-2">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-14 w-full" />
            ))}
          </div>
        ) : recipes.isError ? (
          <p role="alert" className="py-3 text-sm text-destructive">
            {recipeErrorMessage(recipes.error)}
          </p>
        ) : items.length === 0 ? (
          <p className="py-3 text-sm text-muted-foreground">
            {search ? `No recipe matches “${search}”.` : 'No recipes yet.'}
          </p>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {items.map((recipe) => (
              <li key={recipe.id}>
                <button
                  type="button"
                  onClick={() => onPick(recipe)}
                  className="flex min-h-14 w-full items-center gap-3 rounded-md border bg-card p-1.5 text-left hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
                >
                  <RecipePhoto
                    src={recipe.photo_thumb_url}
                    alt={recipe.name}
                    className="size-11 shrink-0 rounded"
                  />
                  <span className="min-w-0 flex-1 truncate text-sm font-medium">{recipe.name}</span>
                  <span className="inline-flex shrink-0 items-center gap-0.5 pr-1 text-xs text-muted-foreground tabular-nums">
                    <Users className="size-3" aria-hidden />
                    {recipe.default_servings}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {recipes.hasNextPage && (
          <Button
            variant="ghost"
            className="mt-1 h-11 w-full"
            disabled={recipes.isFetchingNextPage}
            onClick={() => recipes.fetchNextPage()}
          >
            {recipes.isFetchingNextPage ? 'Loading…' : 'Load more'}
          </Button>
        )}
      </div>
    </div>
  )
}
