import { AlertTriangle, ChevronRight, Save } from 'lucide-react'
import { useMemo } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import {
  shoppingErrorMessage,
  useCreateShoppingList,
  useShoppingLists,
  useShoppingPreview,
  type PreviewItem,
  type ShoppingPreview,
  type ShoppingRange,
} from '@/features/shopping/api'
import {
  DEFAULT_PRESET,
  PRESETS,
  formatRange,
  presetFor,
  rangeError,
} from '@/features/shopping/dates'
import {
  formatBase,
  formatDisplay,
  groupByCategory,
  reasonLabel,
} from '@/features/shopping/format'
import { toMilli } from '@/features/stock/quantity'
import { cn } from '@/lib/utils'

function useRange(): [ShoppingRange, (range: ShoppingRange) => void] {
  const [params, setParams] = useSearchParams()
  const fallback = useMemo(
    () => PRESETS.find((p) => p.id === DEFAULT_PRESET)!.range(new Date()),
    [],
  )
  const range = { from: params.get('from') ?? fallback.from, to: params.get('to') ?? fallback.to }
  const setRange = (next: ShoppingRange) =>
    setParams({ from: next.from, to: next.to }, { replace: true })
  return [range, setRange]
}

export function ShoppingPage() {
  const [range, setRange] = useRange()
  const invalid = rangeError(range)
  const preview = useShoppingPreview(invalid ? null : range)

  return (
    <div className="flex flex-col gap-6">
      <RangePicker range={range} onChange={setRange} error={invalid} />
      {!invalid && <PreviewSection range={range} preview={preview} />}
      <RecentLists />
    </div>
  )
}

function RangePicker({
  range,
  onChange,
  error,
}: {
  range: ShoppingRange
  onChange: (range: ShoppingRange) => void
  error: string | null
}) {
  const active = presetFor(range, new Date())
  return (
    <section aria-label="Date range" className="flex flex-col gap-3">
      <div role="radiogroup" aria-label="Range presets" className="flex gap-2 overflow-x-auto">
        {PRESETS.map((preset) => (
          <button
            key={preset.id}
            type="button"
            role="radio"
            aria-checked={active === preset.id}
            onClick={() => onChange(preset.range(new Date()))}
            className={cn(
              'h-11 shrink-0 rounded-full border px-4 text-sm font-medium focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none',
              active === preset.id
                ? 'border-primary bg-primary text-primary-foreground'
                : 'bg-background hover:bg-muted',
            )}
          >
            {preset.label}
          </button>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="flex min-w-0 flex-col gap-1">
          <Label htmlFor="shopping-from">From</Label>
          <Input
            id="shopping-from"
            type="date"
            value={range.from}
            onChange={(e) => onChange({ ...range, from: e.target.value })}
            className="h-11"
            aria-invalid={error ? true : undefined}
          />
        </div>
        <div className="flex min-w-0 flex-col gap-1">
          <Label htmlFor="shopping-to">To</Label>
          <Input
            id="shopping-to"
            type="date"
            value={range.to}
            min={range.from || undefined}
            onChange={(e) => onChange({ ...range, to: e.target.value })}
            className="h-11"
            aria-invalid={error ? true : undefined}
          />
        </div>
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </section>
  )
}

function PreviewSection({
  range,
  preview,
}: {
  range: ShoppingRange
  preview: ReturnType<typeof useShoppingPreview>
}) {
  if (preview.isPending) {
    return (
      <div role="status" aria-label="Loading preview" className="flex flex-col gap-2">
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-14 w-full" />
        ))}
      </div>
    )
  }
  if (preview.isError) {
    return (
      <div className="flex flex-col items-start gap-3">
        <p role="alert" className="text-sm text-destructive">
          {shoppingErrorMessage(preview.error)}
        </p>
        <Button variant="outline" className="h-11" onClick={() => preview.refetch()}>
          Try again
        </Button>
      </div>
    )
  }
  return (
    <PreviewBody
      range={range}
      preview={preview.data}
      stale={preview.isPlaceholderData || preview.isFetching}
    />
  )
}

function PreviewBody({
  range,
  preview,
  stale,
}: {
  range: ShoppingRange
  preview: ShoppingPreview
  stale: boolean
}) {
  const navigate = useNavigate()
  const create = useCreateShoppingList()
  const toBuy = preview.items.filter((i) => !i.nothing_to_buy)
  const unconverted = preview.items.filter((i) => i.unconverted.length > 0)
  const empty = preview.items.length === 0 && preview.check_have.length === 0
  const groups = groupByCategory(preview.items)

  const onSave = () =>
    create.mutate(range, {
      onSuccess: (list) => {
        toast.success('List saved')
        navigate(`/shopping/lists/${list.id}`)
      },
      onError: (error) =>
        toast.error("Couldn't save the list", { description: shoppingErrorMessage(error) }),
    })

  if (empty) {
    return (
      <div className="rounded-xl border border-dashed p-6 text-center">
        <p className="font-medium">Nothing planned</p>
        <p className="text-sm text-muted-foreground">
          No planned meals between {formatRange(range.from, range.to)}.
        </p>
      </div>
    )
  }

  return (
    <section aria-label="Preview" aria-busy={stale || undefined} className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          {toBuy.length === 0
            ? 'Everything is in stock'
            : `${toBuy.length} ${toBuy.length === 1 ? 'item' : 'items'} to buy`}
        </p>
        <Button className="h-11" disabled={create.isPending || stale} onClick={onSave}>
          <Save aria-hidden /> {create.isPending ? 'Saving…' : 'Save list'}
        </Button>
      </div>

      {groups.map(([name, items]) => (
        <section key={name} aria-label={name} className="flex flex-col gap-1">
          <h2 className="text-sm font-medium text-muted-foreground">{name}</h2>
          <ul className="divide-y rounded-xl border">
            {items.map((item) => (
              <PreviewRow key={item.ingredient_id} item={item} />
            ))}
          </ul>
        </section>
      ))}

      {preview.check_have.length > 0 && (
        <details className="group rounded-xl border">
          <summary className="flex min-h-11 cursor-pointer items-center justify-between gap-2 px-3 py-2 font-medium">
            Check you have ({preview.check_have.length})
            <ChevronRight
              className="size-4 transition-transform group-open:rotate-90"
              aria-hidden
            />
          </summary>
          <ul aria-label="Check you have" className="divide-y border-t">
            {preview.check_have.map((item) => (
              <li key={item.ingredient_id} className="flex flex-col px-3 py-2">
                <span className="font-medium">{item.ingredient_name}</span>
                <span className="text-xs text-muted-foreground">
                  {item.recipe_names.join(', ')}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {unconverted.length > 0 && (
        <section
          aria-label="Couldn't convert"
          className="flex flex-col gap-2 rounded-xl border border-amber-500/40 bg-amber-500/5 p-3"
        >
          <h2 className="flex items-center gap-2 font-medium">
            <AlertTriangle className="size-4 text-amber-600" aria-hidden /> Couldn't convert
          </h2>
          <p className="text-xs text-muted-foreground">
            These amounts are not in the totals above. Fix the recipe's unit or add a conversion
            to the ingredient.
          </p>
          <ul className="flex flex-col gap-2">
            {unconverted.flatMap((item) =>
              item.unconverted.map((line, i) => (
                <li key={`${item.ingredient_id}-${i}`} className="flex flex-col text-sm">
                  <span>
                    <span className="font-medium">{item.ingredient_name}</span> ·{' '}
                    {formatDisplay({ amount: line.amount, unit: line.unit })} ·{' '}
                    {reasonLabel(line.reason)}
                  </span>
                  <span className="flex flex-wrap gap-x-2 text-xs">
                    {uniqueRecipes(line.meals).map((meal) => (
                      <Link
                        key={meal.recipe_id}
                        to={`/recipes/${meal.recipe_id}`}
                        className="text-muted-foreground underline underline-offset-2"
                      >
                        {meal.recipe_name}
                      </Link>
                    ))}
                  </span>
                </li>
              )),
            )}
          </ul>
        </section>
      )}
    </section>
  )
}

function uniqueRecipes<T extends { recipe_id: number }>(meals: T[]): T[] {
  const seen = new Set<number>()
  return meals.filter((m) => !seen.has(m.recipe_id) && seen.add(m.recipe_id))
}

function PreviewRow({ item }: { item: PreviewItem }) {
  const reserved = toMilli(item.reserved_base) > 0n
  const measured = toMilli(item.required_base) > 0n
  return (
    <li className="flex items-center gap-3 px-3 py-2.5">
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="truncate font-medium">{item.ingredient_name}</span>
        {measured && (
          <span className="text-xs text-muted-foreground">
            Need {formatBase(item.required_base, item.dimension)} · In stock{' '}
            {formatBase(item.available_base, item.dimension)}
            {reserved && ` (${formatBase(item.reserved_base, item.dimension)} held for earlier meals)`}
          </span>
        )}
        {item.unconverted.length > 0 && (
          <span className="text-xs text-amber-700 dark:text-amber-400">
            + {item.unconverted.length} unconverted
          </span>
        )}
      </div>
      {item.nothing_to_buy ? (
        <Badge variant="secondary">{measured ? 'In stock' : 'Check'}</Badge>
      ) : (
        <span className="text-right text-sm font-semibold tabular-nums" data-testid="to-buy">
          {formatDisplay(item.display)}
        </span>
      )}
    </li>
  )
}

function RecentLists() {
  const lists = useShoppingLists()
  return (
    <section aria-labelledby="recent-lists" className="flex flex-col gap-2">
      <h2 id="recent-lists" className="font-medium">
        Saved lists
      </h2>
      {lists.isPending ? (
        <div role="status" aria-label="Loading lists" className="flex flex-col gap-2">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      ) : lists.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {shoppingErrorMessage(lists.error)}
        </p>
      ) : lists.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">No saved lists yet.</p>
      ) : (
        <ul className="divide-y rounded-xl border">
          {lists.data.map((list) => (
            <li key={list.id}>
              <Link
                to={`/shopping/lists/${list.id}`}
                className="flex min-h-16 items-center gap-3 px-3 py-2 hover:bg-muted/50 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none focus-visible:ring-inset"
              >
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate font-medium">
                    {formatRange(list.start_date, list.end_date)}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {list.checked_count}/{list.item_count} checked
                  </span>
                </span>
                <Badge variant={list.status === 'done' ? 'secondary' : 'default'}>
                  {list.status === 'done' ? 'Done' : 'Open'}
                </Badge>
                <ChevronRight className="size-4 text-muted-foreground" aria-hidden />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
