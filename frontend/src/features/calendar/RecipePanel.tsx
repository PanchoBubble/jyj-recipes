import { useDraggable, useDroppable } from '@dnd-kit/core'
import { Search, X } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { recipeErrorMessage, useRecipes } from '@/features/recipes/api'
import { RecipePhoto } from '@/features/recipes/RecipePhoto'
import { useDebouncedValue } from '@/features/recipes/useDebouncedValue'
import { cn } from '@/lib/utils'

import { DragHandle } from './DragHandle'
import { dndId, type DragData, type TrayRecipe } from './plan'

/**
 * Always-visible recipes under the week: search plus a grid of cards that scrolls on its own.
 * It is also a drop target, so letting go of a recipe back over it cancels the drag.
 */
export function RecipePanel({ className }: { className?: string }) {
  const { setNodeRef, isOver, active } = useDroppable({ id: dndId.tray, data: { type: 'tray' } })
  const [query, setQuery] = useState('')
  const search = useDebouncedValue(query.trim(), 250)
  const recipes = useRecipes(search)
  const items = recipes.data?.pages.flatMap((p) => p.items) ?? []

  return (
    <aside
      ref={setNodeRef}
      aria-label="Recipes"
      data-bottom-dock=""
      className={cn(
        'flex min-h-0 flex-col gap-2 border-t bg-background px-3 pt-2 shadow-[0_-4px_16px_-8px_rgb(0_0_0/0.2)] transition-colors',
        active && isOver && 'bg-muted',
        className,
      )}
    >
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search
            className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
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
              className="absolute top-0 right-0 inline-flex size-11 items-center justify-center text-muted-foreground"
            >
              <X className="size-4" aria-hidden />
            </button>
          )}
        </div>
        <p className="hidden shrink-0 text-xs text-muted-foreground sm:block">
          {active ? 'Drop here to cancel' : 'Hold ⋮⋮ and drag onto a day'}
        </p>
      </div>
      <div
        data-testid="recipe-list"
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain pb-3"
      >
        {recipes.isPending ? (
          <div className="grid grid-cols-2 gap-1.5 md:grid-cols-3">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-12 w-full" />
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
          <ul aria-label="Recipes to plan" className="grid grid-cols-2 gap-1.5 md:grid-cols-3">
            {items.map((recipe) => (
              <PanelItem key={recipe.id} recipe={recipe} />
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
    </aside>
  )
}

function PanelItem({ recipe }: { recipe: TrayRecipe }) {
  const data: DragData = { type: 'recipe', recipe }
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, isDragging } = useDraggable({
    id: dndId.recipe(recipe.id),
    data,
  })
  return (
    <li ref={setNodeRef} className={cn('min-w-0', isDragging && 'opacity-40')}>
      <PanelRecipeView
        recipe={recipe}
        handleProps={{
          ref: setActivatorNodeRef,
          ...attributes,
          ...listeners,
          'aria-label': `Drag ${recipe.name} onto the calendar`,
        }}
      />
    </li>
  )
}

export function PanelRecipeView({
  recipe,
  handleProps,
  overlay = false,
}: {
  recipe: TrayRecipe
  handleProps?: React.ComponentProps<'button'>
  overlay?: boolean
}) {
  return (
    <div
      className={cn(
        'flex items-center gap-2 rounded-md border bg-card p-1 pl-1.5',
        overlay && 'w-44 shadow-lg ring-2 ring-primary',
      )}
    >
      <RecipePhoto src={recipe.photo_thumb_url} alt={recipe.name} className="size-9 shrink-0 rounded" />
      <span className="line-clamp-2 min-w-0 flex-1 text-xs leading-4 font-medium break-words">
        {recipe.name}
      </span>
      <DragHandle {...handleProps} className="-ml-1" />
    </div>
  )
}
