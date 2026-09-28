import { useDraggable, useDroppable } from '@dnd-kit/core'
import { ChevronDown, ChevronUp, Search, Users, X } from 'lucide-react'
import { useId, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { recipeErrorMessage, useRecipes } from '@/features/recipes/api'
import { RecipePhoto } from '@/features/recipes/RecipePhoto'
import { useDebouncedValue } from '@/features/recipes/useDebouncedValue'
import { cn } from '@/lib/utils'

import { DragHandle } from './SlotCell'
import { dndId, type DragData, type TrayRecipe } from './plan'

interface RecipeTrayProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** A recipe from the tray is being dragged: fold the list away so the calendar shows. */
  folded: boolean
}

export function RecipeTray({ open, onOpenChange, folded }: RecipeTrayProps) {
  const bodyId = useId()
  const { setNodeRef, isOver } = useDroppable({ id: dndId.tray, data: { type: 'tray' } })
  const expanded = open && !folded

  return (
    <aside
      ref={setNodeRef}
      aria-label="Recipe tray"
      className={cn(
        'fixed inset-x-0 bottom-[calc(4rem+env(safe-area-inset-bottom))] z-20 mx-auto max-w-2xl rounded-t-xl border-t bg-background shadow-[0_-4px_16px_-8px_rgb(0_0_0/0.2)] transition-colors',
        folded && isOver && 'bg-muted',
      )}
    >
      {folded ? (
        <p className="flex h-12 items-center justify-center text-sm text-muted-foreground">
          Drop here to cancel
        </p>
      ) : (
        <button
          type="button"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => onOpenChange(!open)}
          className="flex h-12 w-full items-center justify-between gap-2 px-4 text-sm font-medium focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none focus-visible:ring-inset"
        >
          <span>{open ? 'Hide recipes' : 'Recipes'}</span>
          <span className="flex items-center gap-1 text-xs font-normal text-muted-foreground">
            {open ? 'Hold ⋮⋮ and drag onto a day' : 'Drag onto the calendar'}
            {open ? (
              <ChevronDown className="size-4" aria-hidden />
            ) : (
              <ChevronUp className="size-4" aria-hidden />
            )}
          </span>
        </button>
      )}
      {/* Kept mounted while folded: the dragged handle lives in here. */}
      <div id={bodyId} hidden={!open} className={cn(!expanded && 'max-h-0 overflow-hidden')}>
        {open && <TrayBody />}
      </div>
    </aside>
  )
}

function TrayBody() {
  const [query, setQuery] = useState('')
  const search = useDebouncedValue(query.trim(), 250)
  const recipes = useRecipes(search)
  const items = recipes.data?.pages.flatMap((p) => p.items) ?? []

  return (
    <div className="flex flex-col gap-2 px-4 pb-3">
      <div className="relative">
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
      <div data-testid="tray-list" className="max-h-[32svh] overflow-y-auto overscroll-contain">
        {recipes.isPending ? (
          <div className="flex flex-col gap-2">
            {[0, 1, 2].map((i) => (
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
          <ul aria-label="Recipes to plan" className="flex flex-col gap-1.5">
            {items.map((recipe) => (
              <TrayItem key={recipe.id} recipe={recipe} />
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

function TrayItem({ recipe }: { recipe: TrayRecipe }) {
  const data: DragData = { type: 'recipe', recipe }
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, isDragging } = useDraggable({
    id: dndId.recipe(recipe.id),
    data,
  })
  return (
    <li ref={setNodeRef} className={cn(isDragging && 'opacity-40')}>
      <TrayRecipeView
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

export function TrayRecipeView({
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
        overlay && 'shadow-lg ring-2 ring-primary',
      )}
    >
      <RecipePhoto src={recipe.photo_thumb_url} alt={recipe.name} className="size-9 shrink-0 rounded" />
      <span className="min-w-0 flex-1 truncate text-sm font-medium">{recipe.name}</span>
      <span className="inline-flex shrink-0 items-center gap-0.5 text-xs text-muted-foreground tabular-nums">
        <Users className="size-3" aria-hidden />
        {recipe.default_servings}
      </span>
      <DragHandle {...handleProps} />
    </div>
  )
}
