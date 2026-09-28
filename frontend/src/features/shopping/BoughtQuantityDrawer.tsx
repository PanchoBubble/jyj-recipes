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
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { convertibleUnits, useIngredients, useUnits } from '@/features/ingredients/api'
import { useUpdateShoppingItem, type ShoppingListItem } from '@/features/shopping/api'
import { formatDisplay } from '@/features/shopping/format'
import { AMOUNT_RE, normaliseAmount, trimAmount } from '@/features/stock/quantity'

interface Props {
  listId: number
  item: ShoppingListItem | null
  onOpenChange: (open: boolean) => void
}

export function BoughtQuantityDrawer({ listId, item, onOpenChange }: Props) {
  // Keep rendering the last item while the drawer animates closed.
  const [shown, setShown] = useState(item)
  if (item && item !== shown) setShown(item)

  return (
    <Drawer open={item !== null} onOpenChange={onOpenChange}>
      <DrawerContent>
        {shown && (
          <BoughtForm
            key={shown.id}
            listId={listId}
            item={shown}
            onDone={() => onOpenChange(false)}
          />
        )}
      </DrawerContent>
    </Drawer>
  )
}

function useUnitOptions(item: ShoppingListItem): string[] {
  const units = useUnits()
  const ingredients = useIngredients()
  const ingredient = ingredients.data?.find((i) => i.id === item.ingredient_id)
  const codes =
    units.data && ingredient ? convertibleUnits(units.data, ingredient).map((u) => u.code) : []
  const current = item.bought_display?.unit ?? item.display.unit
  return codes.includes(current) ? codes : [current, ...codes]
}

function BoughtForm({
  listId,
  item,
  onDone,
}: {
  listId: number
  item: ShoppingListItem
  onDone: () => void
}) {
  const update = useUpdateShoppingItem(listId, item.id)
  const options = useUnitOptions(item)
  const start = item.bought_display ?? (item.kind === 'buy' ? item.display : null)
  const [amount, setAmount] = useState(start ? trimAmount(start.amount) : '')
  const [unit, setUnit] = useState(start?.unit ?? item.display.unit)
  const [error, setError] = useState<string | null>(null)

  const onSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    if (!AMOUNT_RE.test(amount.trim())) {
      setError('Enter an amount like 250 or 1.5 (up to 3 decimals)')
      return
    }
    update.mutate({
      item,
      changes: { checked: true, bought_quantity: normaliseAmount(amount), unit },
    })
    onDone()
  }

  const onClear = () => {
    update.mutate({ item, changes: { bought_quantity: null } })
    onDone()
  }

  return (
    <div className="flex min-h-0 flex-col overflow-y-auto pb-[env(safe-area-inset-bottom)]">
      <DrawerHeader className="text-left group-data-[vaul-drawer-direction=bottom]/drawer-content:text-left">
        <DrawerTitle className="truncate text-lg">{item.ingredient_name}</DrawerTitle>
        <DrawerDescription>
          {item.kind === 'buy'
            ? `List says ${formatDisplay(item.display)}. Enter what you actually bought.`
            : 'Enter how much you bought to add it to the pantry.'}
        </DrawerDescription>
      </DrawerHeader>
      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-2 px-4 pb-4">
        <Label htmlFor="bought-amount">Bought</Label>
        <div className="flex gap-2">
          <Input
            id="bought-amount"
            inputMode="decimal"
            autoComplete="off"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? 'bought-error' : undefined}
            className="h-11 flex-1"
          />
          <NativeSelect
            aria-label="Unit"
            value={unit}
            onChange={(e) => setUnit(e.target.value)}
            className="w-28 [&_select]:h-11"
          >
            {options.map((code) => (
              <NativeSelectOption key={code} value={code}>
                {code.replaceAll('_', ' ')}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </div>
        {error && (
          <p id="bought-error" role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <Button type="submit" className="h-11">
          Save bought amount
        </Button>
        {item.bought_display && (
          <Button type="button" variant="ghost" className="h-11" onClick={onClear}>
            Use the list amount
          </Button>
        )}
      </form>
    </div>
  )
}
