import { BookOpen, Plus, Search, X } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { recipeErrorMessage, useRecipes, type RecipeSummary } from '@/features/recipes/api'
import { RecipePhoto } from '@/features/recipes/RecipePhoto'
import { useDebouncedValue } from '@/features/recipes/useDebouncedValue'

export function RecipesPage() {
  const [query, setQuery] = useState('')
  const search = useDebouncedValue(query.trim(), 250)
  const recipes = useRecipes(search)
  const items = recipes.data?.pages.flatMap((p) => p.items) ?? []
  const total = recipes.data?.pages.at(-1)?.total ?? 0

  return (
    <div className="flex flex-col gap-3">
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

      {recipes.isPending ? (
        <ListSkeleton />
      ) : recipes.isError ? (
        <EmptyState
          title="Couldn't load recipes"
          body={recipeErrorMessage(recipes.error)}
          action={
            <Button variant="outline" className="h-11" onClick={() => recipes.refetch()}>
              Try again
            </Button>
          }
        />
      ) : items.length === 0 ? (
        search ? (
          <EmptyState
            title="No matches"
            body={`No recipe matches “${search}”.`}
            action={
              <Button variant="outline" className="h-11" onClick={() => setQuery('')}>
                Clear search
              </Button>
            }
          />
        ) : (
          <EmptyState
            title="No recipes yet"
            body="Add the dishes you cook so you can plan meals and shop for them."
            action={
              <Button asChild className="h-11">
                <Link to="/recipes/new">
                  <Plus aria-hidden /> New recipe
                </Link>
              </Button>
            }
          />
        )
      ) : (
        <>
          <ul
            aria-label="Recipes"
            aria-busy={recipes.isPlaceholderData || undefined}
            className="flex flex-col gap-2"
          >
            {items.map((recipe) => (
              <RecipeCard key={recipe.id} recipe={recipe} />
            ))}
          </ul>
          {recipes.hasNextPage ? (
            <Button
              variant="outline"
              className="h-11"
              disabled={recipes.isFetchingNextPage}
              onClick={() => recipes.fetchNextPage()}
            >
              {recipes.isFetchingNextPage ? 'Loading…' : `Load more (${items.length} of ${total})`}
            </Button>
          ) : null}
        </>
      )}

      <Button
        asChild
        size="icon"
        className="fixed right-[max(1rem,env(safe-area-inset-right))] bottom-[calc(5rem+env(safe-area-inset-bottom))] z-10 size-14 rounded-full shadow-lg"
      >
        <Link to="/recipes/new" aria-label="New recipe">
          <Plus className="size-6" aria-hidden />
        </Link>
      </Button>
    </div>
  )
}

function RecipeCard({ recipe }: { recipe: RecipeSummary }) {
  const count = recipe.ingredient_count
  return (
    <li>
      <Link
        to={`/recipes/${recipe.id}`}
        className="flex min-h-20 items-center gap-3 rounded-xl border p-2 pr-3 hover:bg-muted/50 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        <RecipePhoto
          src={recipe.photo_thumb_url ?? recipe.photo_url}
          alt={recipe.name}
          width={64}
          height={64}
          className="size-16 shrink-0 rounded-lg"
        />
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="truncate font-medium">{recipe.name}</span>
          {recipe.description && (
            <span className="line-clamp-2 text-sm text-muted-foreground">{recipe.description}</span>
          )}
          <span className="text-xs text-muted-foreground">
            {count} {count === 1 ? 'ingredient' : 'ingredients'}
          </span>
        </span>
      </Link>
    </li>
  )
}

function ListSkeleton() {
  return (
    <div aria-label="Loading recipes" role="status" className="flex flex-col gap-2">
      {Array.from({ length: 5 }, (_, i) => (
        <div key={i} className="flex items-center gap-3 rounded-xl border p-2">
          <Skeleton className="size-16 rounded-lg" />
          <div className="flex flex-1 flex-col gap-2">
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-3 w-3/4" />
          </div>
        </div>
      ))}
    </div>
  )
}

function EmptyState({
  title,
  body,
  action,
}: {
  title: string
  body: string
  action?: React.ReactNode
}) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed px-6 py-10 text-center">
      <BookOpen className="size-10 text-muted-foreground" aria-hidden />
      <div className="flex flex-col gap-1">
        <p className="font-medium">{title}</p>
        <p className="text-sm text-muted-foreground">{body}</p>
      </div>
      {action}
    </div>
  )
}
