import { PackageOpen, Plus, Search, X } from 'lucide-react'
import { useDeferredValue, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  IngredientFormDrawer,
  type IngredientFormTarget,
} from '@/features/ingredients/IngredientFormDrawer'
import { useIngredients, type Ingredient } from '@/features/ingredients/api'
import { StockDetailDrawer } from '@/features/stock/StockDetailDrawer'
import { StockRow } from '@/features/stock/StockRow'
import { stockErrorMessage } from '@/features/stock/api'
import { cn } from '@/lib/utils'

const UNCATEGORISED = 'Other'
const ALL = '__all__'

function categoryOf(ingredient: Ingredient) {
  return ingredient.category ?? UNCATEGORISED
}

function groupByCategory(ingredients: Ingredient[]) {
  const groups = new Map<string, Ingredient[]>()
  for (const ingredient of ingredients) {
    const key = categoryOf(ingredient)
    groups.set(key, [...(groups.get(key) ?? []), ingredient])
  }
  return [...groups.entries()].sort(([a], [b]) => {
    if (a === UNCATEGORISED) return 1
    if (b === UNCATEGORISED) return -1
    return a.localeCompare(b)
  })
}


export function StockPage() {
  const ingredients = useIngredients()
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState(ALL)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [editing, setEditing] = useState<IngredientFormTarget | null>(null)
  const search = useDeferredValue(query.trim().toLowerCase())

  const all = useMemo(() => ingredients.data ?? [], [ingredients.data])
  const categories = useMemo(
    () => groupByCategory(all).map(([name]) => name),
    [all],
  )
  const visible = useMemo(
    () =>
      all.filter(
        (i) =>
          (category === ALL || categoryOf(i) === category) &&
          (!search || i.name.toLowerCase().includes(search)),
      ),
    [all, category, search],
  )
  const groups = useMemo(() => groupByCategory(visible), [visible])
  const selected = all.find((i) => i.id === selectedId) ?? null

  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search
            className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            type="search"
            aria-label="Search ingredients"
            placeholder="Search ingredients"
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
        <Button
          className="h-11 px-3"
          onClick={() => setEditing({ ingredient: null })}
          aria-label="Add ingredient"
        >
          <Plus className="size-5" aria-hidden />
          <span className="hidden min-[380px]:inline">Add</span>
        </Button>
      </div>

      {categories.length > 1 && (
        <div
          role="radiogroup"
          aria-label="Category"
          className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1 [scrollbar-width:none]"
        >
          {[ALL, ...categories].map((name) => (
            <button
              key={name}
              type="button"
              role="radio"
              aria-checked={category === name}
              onClick={() => setCategory(name)}
              className={cn(
                'h-11 shrink-0 rounded-full border px-4 text-sm font-medium whitespace-nowrap transition-colors focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none',
                category === name
                  ? 'border-primary bg-primary text-primary-foreground'
                  : 'bg-background text-muted-foreground hover:text-foreground',
              )}
            >
              {name === ALL ? 'All' : name}
            </button>
          ))}
        </div>
      )}

      {ingredients.isPending ? (
        <ListSkeleton />
      ) : ingredients.isError ? (
        <EmptyState
          title="Couldn't load stock"
          body={stockErrorMessage(ingredients.error)}
          action={
            <Button variant="outline" className="h-11" onClick={() => ingredients.refetch()}>
              Try again
            </Button>
          }
        />
      ) : all.length === 0 ? (
        <EmptyState
          title="No ingredients yet"
          body="Add the things you keep in the kitchen to start tracking stock."
          action={
            <Button className="h-11" onClick={() => setEditing({ ingredient: null })}>
              <Plus aria-hidden /> Add ingredient
            </Button>
          }
        />
      ) : groups.length === 0 ? (
        <EmptyState
          title="No matches"
          body={query ? `Nothing matches “${query.trim()}”.` : 'Nothing in this category.'}
          action={
            <Button
              variant="outline"
              className="h-11"
              onClick={() => {
                setQuery('')
                setCategory(ALL)
              }}
            >
              Clear filters
            </Button>
          }
        />
      ) : (
        <div className="flex flex-col gap-4">
          {groups.map(([name, items]) => (
            <section key={name} aria-label={name}>
              <h2 className="mb-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                {name}
              </h2>
              <ul className="divide-y rounded-xl border">
                {items.map((ingredient) => (
                  <StockRow
                    key={ingredient.id}
                    ingredient={ingredient}
                    onOpen={() => setSelectedId(ingredient.id)}
                  />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}

      <StockDetailDrawer
        ingredient={selected}
        onOpenChange={(open) => !open && setSelectedId(null)}
        onEdit={(ingredient) => {
          setSelectedId(null)
          setEditing({ ingredient })
        }}
      />
      <IngredientFormDrawer
        target={editing}
        categories={categories.filter((c) => c !== UNCATEGORISED)}
        onOpenChange={(open) => !open && setEditing(null)}
      />
    </div>
  )
}

function ListSkeleton() {
  return (
    <div aria-label="Loading stock" role="status" className="flex flex-col gap-2">
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className="flex items-center gap-3 rounded-xl border p-3">
          <div className="flex flex-1 flex-col gap-2">
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-3 w-1/4" />
          </div>
          <Skeleton className="size-11 rounded-lg" />
          <Skeleton className="size-11 rounded-lg" />
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
      <PackageOpen className="size-10 text-muted-foreground" aria-hidden />
      <div className="flex flex-col gap-1">
        <p className="font-medium">{title}</p>
        <p className="text-sm text-muted-foreground">{body}</p>
      </div>
      {action}
    </div>
  )
}
