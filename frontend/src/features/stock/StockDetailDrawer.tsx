import { Pencil } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
} from '@/components/ui/drawer'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import { useMe } from '@/features/auth/api'
import { convertibleUnits, useUnits, type Ingredient } from '@/features/ingredients/api'
import {
  stockErrorMessage,
  useSetStock,
  useStockMovements,
  type StockMovement,
  type StockReason,
} from '@/features/stock/api'
import {
  AMOUNT_RE,
  displayFromBase,
  formatQuantity,
  normaliseAmount,
  toMilli,
  trimAmount,
} from '@/features/stock/quantity'

interface Props {
  ingredient: Ingredient | null
  onOpenChange: (open: boolean) => void
  onEdit: (ingredient: Ingredient) => void
}

export function StockDetailDrawer({ ingredient, onOpenChange, onEdit }: Props) {
  // Keep rendering the last ingredient while the drawer animates closed.
  const [shown, setShown] = useState(ingredient)
  if (ingredient && ingredient !== shown) setShown(ingredient)

  return (
    <Drawer open={ingredient !== null} onOpenChange={onOpenChange}>
      <DrawerContent>
        {shown && <DetailBody ingredient={shown} onEdit={onEdit} />}
      </DrawerContent>
    </Drawer>
  )
}

function DetailBody({
  ingredient,
  onEdit,
}: {
  ingredient: Ingredient
  onEdit: (ingredient: Ingredient) => void
}) {
  const { display } = ingredient.stock
  return (
    <div className="flex min-h-0 flex-col overflow-y-auto pb-[env(safe-area-inset-bottom)]">
      <DrawerHeader className="flex-row items-start justify-between gap-2 text-left group-data-[vaul-drawer-direction=bottom]/drawer-content:text-left">
        <div className="min-w-0">
          <DrawerTitle className="truncate text-lg">{ingredient.name}</DrawerTitle>
          <DrawerDescription>
            In stock: <span className="font-medium text-foreground">{formatQuantity(display.amount, display.unit)}</span>
          </DrawerDescription>
        </div>
        <Button
          variant="ghost"
          className="h-11 shrink-0"
          onClick={() => onEdit(ingredient)}
        >
          <Pencil aria-hidden /> Edit
        </Button>
      </DrawerHeader>
      <div className="flex flex-col gap-6 px-4 pb-4">
        <SetExactForm key={ingredient.id} ingredient={ingredient} />
        <MovementHistory ingredient={ingredient} />
      </div>
    </div>
  )
}

function SetExactForm({ ingredient }: { ingredient: Ingredient }) {
  const units = useUnits()
  const setStock = useSetStock(ingredient.id)
  const [amount, setAmount] = useState(() => trimAmount(ingredient.stock.display.amount))
  const [unit, setUnit] = useState(ingredient.stock.display.unit)
  const [error, setError] = useState<string | null>(null)
  const options = units.data ? convertibleUnits(units.data, ingredient) : []

  const onSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    if (!AMOUNT_RE.test(amount.trim())) {
      setError('Enter an amount like 250 or 1.5 (up to 3 decimals)')
      return
    }
    setError(null)
    setStock.mutate(
      { amount: normaliseAmount(amount), unit },
      {
        onSuccess: () => toast.success(`${ingredient.name} updated`),
        onError: (e) => setError(stockErrorMessage(e)),
      },
    )
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-2">
      <Label htmlFor="stock-set-amount">Set exact amount</Label>
      <div className="flex gap-2">
        <Input
          id="stock-set-amount"
          inputMode="decimal"
          autoComplete="off"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? 'stock-set-error' : undefined}
          className="h-11 flex-1"
        />
        <NativeSelect
          aria-label="Unit"
          value={unit}
          onChange={(e) => setUnit(e.target.value)}
          className="w-28 [&_select]:h-11"
        >
          {options.length === 0 && <NativeSelectOption value={unit}>{unit}</NativeSelectOption>}
          {options.map((u) => (
            <NativeSelectOption key={u.code} value={u.code}>
              {u.code}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      </div>
      {error && (
        <p id="stock-set-error" role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <Button type="submit" className="h-11" disabled={setStock.isPending}>
        {setStock.isPending ? 'Saving…' : 'Save amount'}
      </Button>
    </form>
  )
}

const REASONS: Record<StockReason, string> = {
  manual: 'Adjusted',
  cooked: 'Cooked',
  purchased: 'Bought',
  correction: 'Set',
  undo: 'Undo',
}

const timeFormat = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' })
const dateFormat = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })

function formatWhen(iso: string, now = new Date()) {
  const date = new Date(iso)
  const days = Math.round(
    (new Date(now.toDateString()).getTime() - new Date(date.toDateString()).getTime()) / 86_400_000,
  )
  const day = days === 0 ? 'Today' : days === 1 ? 'Yesterday' : dateFormat.format(date)
  return `${day} ${timeFormat.format(date)}`
}

function MovementHistory({ ingredient }: { ingredient: Ingredient }) {
  const movements = useStockMovements(ingredient.id)
  const me = useMe()

  return (
    <section aria-label="History" className="flex flex-col gap-2">
      <h3 className="text-sm font-medium">History</h3>
      {movements.isPending ? (
        <div className="flex flex-col gap-2" role="status" aria-label="Loading history">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : movements.isError ? (
        <p className="text-sm text-destructive">{stockErrorMessage(movements.error)}</p>
      ) : movements.data.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No changes yet.</p>
      ) : (
        <ul className="divide-y rounded-xl border">
          {movements.data.items.map((m) => (
            <MovementRow
              key={m.id}
              movement={m}
              ingredient={ingredient}
              who={m.user_id === me.data?.id ? 'You' : `User ${m.user_id}`}
            />
          ))}
        </ul>
      )}
      {movements.data && movements.data.total > movements.data.items.length && (
        <p className="text-xs text-muted-foreground">
          Showing the latest {movements.data.items.length} of {movements.data.total}.
        </p>
      )}
    </section>
  )
}

function MovementRow({
  movement,
  ingredient,
  who,
}: {
  movement: StockMovement
  ingredient: Ingredient
  who: string
}) {
  const delta = toMilli(movement.delta_base)
  const shown = displayFromBase(delta, ingredient.dimension)
  const shortfall = toMilli(movement.shortfall_base)
  const short = displayFromBase(shortfall, ingredient.dimension)
  const sign = delta > 0n ? '+' : ''

  return (
    <li className="flex items-center gap-3 px-3 py-2">
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="flex items-center gap-2 text-sm font-medium">
          {REASONS[movement.reason]}
          <Badge variant="secondary">{movement.source === 'chat' ? 'chat' : 'app'}</Badge>
        </span>
        <span className="truncate text-xs text-muted-foreground">
          {who} · {formatWhen(movement.created_at)}
        </span>
        {shortfall > 0n && (
          <span className="text-xs text-destructive">
            Short by {formatQuantity(short.amount, short.unit)}
          </span>
        )}
      </div>
      <span
        className={
          delta < 0n ? 'text-sm font-medium tabular-nums' : 'text-sm font-medium text-emerald-700 tabular-nums dark:text-emerald-400'
        }
      >
        {sign}
        {formatQuantity(shown.amount, shown.unit)}
      </span>
    </li>
  )
}
