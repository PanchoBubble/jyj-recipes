import { ChevronRight, Minus, Plus } from 'lucide-react'

import { Button } from '@/components/ui/button'
import type { Ingredient } from '@/features/ingredients/api'
import { useAdjustStock } from '@/features/stock/api'
import { formatQuantity, stepFor, toMilli } from '@/features/stock/quantity'

export function StockRow({ ingredient, onOpen }: { ingredient: Ingredient; onOpen: () => void }) {
  const adjust = useAdjustStock()
  const step = stepFor(ingredient.dimension)
  const quantity = formatQuantity(ingredient.stock.display.amount, ingredient.stock.display.unit)
  const empty = toMilli(ingredient.stock.quantity_base) === 0n
  const stepLabel = step ? formatQuantity(step.amount, step.unit) : ''

  return (
    <li className="flex items-center gap-1 pr-2">
      <button
        type="button"
        onClick={onOpen}
        className="flex min-h-14 min-w-0 flex-1 items-center gap-2 py-2 pl-3 text-left focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none focus-visible:ring-inset"
      >
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="truncate font-medium">{ingredient.name}</span>
          <span
            className={empty ? 'text-sm text-destructive' : 'text-sm text-muted-foreground'}
            data-testid="stock-quantity"
          >
            {empty ? `Out · ${quantity}` : quantity}
          </span>
        </span>
        <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      </button>
      {step && (
        <>
          <Button
            variant="outline"
            size="icon"
            className="size-11"
            aria-label={`Remove ${stepLabel} ${ingredient.name}`}
            disabled={empty}
            onClick={() =>
              adjust.mutate({ ingredient, delta: `-${step.amount}`, unit: step.unit })
            }
          >
            <Minus className="size-5" aria-hidden />
          </Button>
          <Button
            variant="outline"
            size="icon"
            className="size-11"
            aria-label={`Add ${stepLabel} ${ingredient.name}`}
            onClick={() => adjust.mutate({ ingredient, delta: step.amount, unit: step.unit })}
          >
            <Plus className="size-5" aria-hidden />
          </Button>
        </>
      )}
    </li>
  )
}
