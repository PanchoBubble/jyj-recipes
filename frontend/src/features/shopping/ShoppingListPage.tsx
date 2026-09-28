import { ArrowLeft, Check, CloudOff, Loader2, Pencil, Trash2 } from 'lucide-react'
import { useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { toast } from 'sonner'

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { BoughtQuantityDrawer } from '@/features/shopping/BoughtQuantityDrawer'
import {
  purchasedQuantity,
  shoppingErrorMessage,
  useCompleteShoppingList,
  useDeleteShoppingList,
  usePendingItems,
  useShoppingList,
  useUpdateShoppingItem,
  type PendingItem,
  type ShoppingList,
  type ShoppingListItem,
} from '@/features/shopping/api'
import { formatRange } from '@/features/shopping/dates'
import { formatDisplay, groupByCategory } from '@/features/shopping/format'
import { cn } from '@/lib/utils'

export function ShoppingListPage() {
  const id = Number(useParams().id)
  const list = useShoppingList(id)

  if (list.isPending) {
    return (
      <div role="status" aria-label="Loading list" className="flex flex-col gap-2">
        <Skeleton className="h-8 w-2/3" />
        {Array.from({ length: 5 }, (_, i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    )
  }
  if (list.isError) {
    return (
      <div className="flex flex-col items-start gap-3">
        <BackLink />
        <p role="alert" className="text-sm text-destructive">
          {shoppingErrorMessage(list.error)}
        </p>
        <Button variant="outline" className="h-11" onClick={() => list.refetch()}>
          Try again
        </Button>
      </div>
    )
  }
  return <Checklist list={list.data} />
}

function BackLink() {
  return (
    <Link
      to="/shopping"
      className="-ml-2 inline-flex h-11 w-fit items-center gap-1 rounded-lg px-2 text-sm text-muted-foreground hover:text-foreground"
    >
      <ArrowLeft className="size-4" aria-hidden /> Shopping
    </Link>
  )
}

function Checklist({ list }: { list: ShoppingList }) {
  const done = list.status === 'done'
  const pending = usePendingItems(list.id)
  const [editing, setEditing] = useState<ShoppingListItem | null>(null)
  const measured = list.items.filter((i) => i.kind !== 'check_have')
  const reminders = list.items.filter((i) => i.kind === 'check_have')
  const checked = list.items.filter((i) => i.checked).length

  return (
    <article className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-2">
        <BackLink />
        <DeleteList list={list} />
      </div>

      <header className="flex flex-col gap-1">
        <h2 className="flex items-center gap-2 text-xl font-semibold">
          {formatRange(list.start_date, list.end_date)}
          {done && <Badge variant="secondary">Done</Badge>}
        </h2>
        <p className="text-sm text-muted-foreground">
          {checked}/{list.items.length} checked
          {done && ' · read-only'}
        </p>
      </header>

      {list.items.length === 0 && (
        <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">
          Nothing to buy on this list.
        </p>
      )}

      {groupByCategory(measured).map(([name, items]) => (
        <ItemGroup
          key={name}
          name={name}
          items={items}
          listId={list.id}
          done={done}
          pending={pending}
          onEdit={setEditing}
        />
      ))}

      {reminders.length > 0 && (
        <ItemGroup
          name="Check you have"
          items={reminders}
          listId={list.id}
          done={done}
          pending={pending}
          onEdit={setEditing}
        />
      )}

      {!done && list.items.length > 0 && (
        <CompleteShopping list={list} saving={pending.size > 0} />
      )}

      {!done && (
        <BoughtQuantityDrawer
          listId={list.id}
          item={editing}
          onOpenChange={(open) => !open && setEditing(null)}
        />
      )}
    </article>
  )
}

function ItemGroup({
  name,
  items,
  listId,
  done,
  pending,
  onEdit,
}: {
  name: string
  items: ShoppingListItem[]
  listId: number
  done: boolean
  pending: Map<number, PendingItem>
  onEdit: (item: ShoppingListItem) => void
}) {
  return (
    <section aria-label={name} className="flex flex-col gap-1">
      <h3 className="text-sm font-medium text-muted-foreground">{name}</h3>
      <ul className="divide-y rounded-xl border">
        {items.map((item) => (
          <ItemRow
            key={item.id}
            item={item}
            listId={listId}
            done={done}
            pending={pending.get(item.id)}
            onEdit={onEdit}
          />
        ))}
      </ul>
    </section>
  )
}

const LONG_PRESS_MS = 500

function useLongPress(onLongPress: () => void, enabled: boolean) {
  const timer = useRef<number | null>(null)
  const fired = useRef(false)
  const clear = () => {
    if (timer.current !== null) window.clearTimeout(timer.current)
    timer.current = null
  }
  if (!enabled) return { handlers: {}, consumeLongPress: () => false }
  return {
    handlers: {
      onPointerDown: () => {
        fired.current = false
        clear()
        timer.current = window.setTimeout(() => {
          fired.current = true
          onLongPress()
        }, LONG_PRESS_MS)
      },
      onPointerUp: clear,
      onPointerLeave: clear,
      onPointerCancel: clear,
      onContextMenu: (e: React.MouseEvent) => e.preventDefault(),
    },
    /** True once after a long press, so the click that ends it does not also toggle. */
    consumeLongPress: () => {
      const was = fired.current
      fired.current = false
      return was
    },
  }
}

function itemAmount(item: ShoppingListItem): string | null {
  if (item.kind === 'buy') return formatDisplay(item.display)
  if (item.kind === 'unconverted') {
    return item.unconverted.map((u) => formatDisplay({ amount: u.amount, unit: u.unit })).join(' + ')
  }
  return null
}

function ItemRow({
  item,
  listId,
  done,
  pending,
  onEdit,
}: {
  item: ShoppingListItem
  listId: number
  done: boolean
  pending: PendingItem | undefined
  onEdit: (item: ShoppingListItem) => void
}) {
  const update = useUpdateShoppingItem(listId, item.id)
  const { handlers, consumeLongPress } = useLongPress(() => onEdit(item), !done)
  const amount = itemAmount(item)
  const retrying = pending && (pending.paused || pending.failureCount > 0)

  const onToggle = () => {
    if (consumeLongPress()) return
    update.mutate({ item, changes: { checked: !item.checked } })
  }

  return (
    <li className="flex items-stretch">
      <button
        type="button"
        role="checkbox"
        aria-checked={item.checked}
        aria-disabled={done || undefined}
        disabled={done}
        onClick={onToggle}
        {...handlers}
        className={cn(
          'flex min-h-16 min-w-0 flex-1 touch-manipulation items-center gap-3 px-3 py-2 text-left select-none focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none focus-visible:ring-inset',
          !done && 'active:bg-muted/60',
        )}
      >
        <span
          aria-hidden
          className={cn(
            'flex size-7 shrink-0 items-center justify-center rounded-md border-2',
            item.checked ? 'border-primary bg-primary text-primary-foreground' : 'border-input',
          )}
        >
          {item.checked && <Check className="size-5" strokeWidth={3} />}
        </span>
        <span className="flex min-w-0 flex-1 flex-col">
          <span
            className={cn(
              'truncate font-medium',
              item.checked && 'text-muted-foreground line-through',
            )}
          >
            {item.ingredient_name}
          </span>
          {item.bought_display ? (
            <span className="text-xs text-emerald-700 dark:text-emerald-400">
              Bought {formatDisplay(item.bought_display)}
            </span>
          ) : item.kind !== 'buy' ? (
            <span className="truncate text-xs text-muted-foreground">
              {item.kind === 'unconverted' ? "Couldn't convert · " : ''}
              {item.recipe_names.join(', ')}
            </span>
          ) : null}
        </span>
        {amount && (
          <span className="shrink-0 text-right text-sm font-semibold tabular-nums">{amount}</span>
        )}
        {pending && (
          <span
            className="shrink-0 text-muted-foreground"
            role="status"
            aria-label={retrying ? 'Not saved yet, retrying' : 'Saving'}
          >
            {retrying ? (
              <CloudOff className="size-4 text-amber-600" aria-hidden />
            ) : (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            )}
          </span>
        )}
      </button>
      {!done && (
        <Button
          variant="ghost"
          size="icon"
          className="h-auto min-h-16 w-12 shrink-0 rounded-none"
          aria-label={`Edit bought amount for ${item.ingredient_name}`}
          onClick={() => onEdit(item)}
        >
          <Pencil className="size-4" aria-hidden />
        </Button>
      )}
    </li>
  )
}

function CompleteShopping({ list, saving }: { list: ShoppingList; saving: boolean }) {
  const complete = useCompleteShoppingList(list.id)
  const [open, setOpen] = useState(false)
  const adding = list.items.flatMap((item) => {
    const quantity = purchasedQuantity(item)
    return quantity ? [{ item, quantity }] : []
  })
  const skipped = list.items.filter((i) => !i.checked).length

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) complete.reset()
      }}
    >
      <AlertDialogTrigger asChild>
        <Button className="h-12 text-base" disabled={saving}>
          {saving ? 'Saving changes…' : 'Complete shopping'}
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Complete shopping?</AlertDialogTitle>
          <AlertDialogDescription>
            {adding.length === 0
              ? 'Nothing will be added to the pantry. The list becomes read-only.'
              : 'This adds the following to the pantry and makes the list read-only.'}
          </AlertDialogDescription>
        </AlertDialogHeader>
        {adding.length > 0 && (
          <ul aria-label="Adding to pantry" className="max-h-64 divide-y overflow-y-auto rounded-lg border text-sm">
            {adding.map(({ item, quantity }) => (
              <li key={item.id} className="flex justify-between gap-2 px-3 py-2">
                <span className="truncate">{item.ingredient_name}</span>
                <span className="shrink-0 font-medium tabular-nums">+{formatDisplay(quantity)}</span>
              </li>
            ))}
          </ul>
        )}
        {skipped > 0 && (
          <p className="text-sm text-muted-foreground">
            {skipped} unchecked {skipped === 1 ? 'item is' : 'items are'} not added.
          </p>
        )}
        {complete.isError && (
          <p role="alert" className="text-sm text-destructive">
            {shoppingErrorMessage(complete.error)}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel className="h-11">Cancel</AlertDialogCancel>
          <Button
            className="h-11"
            disabled={complete.isPending}
            onClick={() => complete.mutate(undefined, { onSuccess: () => setOpen(false) })}
          >
            {complete.isPending ? 'Completing…' : 'Complete'}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

function DeleteList({ list }: { list: ShoppingList }) {
  const navigate = useNavigate()
  const remove = useDeleteShoppingList(list.id)
  const [open, setOpen] = useState(false)

  const onDelete = () =>
    remove.mutate(undefined, {
      onSuccess: () => {
        setOpen(false)
        toast.success('List deleted')
        navigate('/shopping', { replace: true })
      },
    })

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) remove.reset()
      }}
    >
      <AlertDialogTrigger asChild>
        <Button variant="ghost" className="h-11 text-destructive">
          <Trash2 aria-hidden /> Delete
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete this list?</AlertDialogTitle>
          <AlertDialogDescription>
            {list.status === 'done'
              ? 'What it already added to the pantry stays there.'
              : 'Nothing has been added to the pantry from it yet.'}
          </AlertDialogDescription>
        </AlertDialogHeader>
        {remove.isError && (
          <p role="alert" className="text-sm text-destructive">
            {shoppingErrorMessage(remove.error)}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel className="h-11">Cancel</AlertDialogCancel>
          <Button
            variant="destructive"
            className="h-11"
            disabled={remove.isPending}
            onClick={onDelete}
          >
            {remove.isPending ? 'Deleting…' : 'Delete'}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
