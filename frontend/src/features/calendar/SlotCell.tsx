import { useDroppable } from '@dnd-kit/core'
import { SortableContext, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Check, GripVertical, Plus, Users } from 'lucide-react'
import type { ComponentProps } from 'react'

import { RecipePhoto } from '@/features/recipes/RecipePhoto'
import { cn } from '@/lib/utils'

import { formatLongDay } from './dates'
import { dndId, isPending, type Cell, type DragData, type MealSlot, type PlannedMeal } from './plan'

interface SlotCellProps {
  cell: Cell
  slot: MealSlot
  meals: PlannedMeal[]
  onOpenMeal: (meal: PlannedMeal) => void
  onAdd: (cell: Cell) => void
}

export function SlotCell({ cell, slot, meals, onOpenMeal, onAdd }: SlotCellProps) {
  const disabled = !slot.active
  const { setNodeRef, isOver, active } = useDroppable({
    id: dndId.cell(cell),
    data: { type: 'cell', cell, disabled },
    disabled,
  })
  const label = `${formatLongDay(cell.date)}, ${slot.name}`
  const dragging = active !== null

  return (
    <section
      ref={setNodeRef}
      aria-label={label}
      data-cell={dndId.cell(cell)}
      className={cn(
        'flex flex-col gap-1.5 rounded-lg border border-transparent p-1.5 transition-colors',
        dragging && !disabled && 'border-dashed border-border',
        isOver && !disabled && 'border-solid border-primary bg-primary/5',
      )}
    >
      <div className="flex items-center justify-between gap-2 pl-1">
        <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          {slot.name}
          {disabled && <span className="normal-case"> (inactive)</span>}
        </h3>
        {meals.length > 0 && !disabled && (
          <button
            type="button"
            aria-label={`Add a recipe to ${label}`}
            onClick={() => onAdd(cell)}
            className="-my-2 inline-flex size-9 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
          >
            <Plus className="size-4" aria-hidden />
          </button>
        )}
      </div>

      <SortableContext items={meals.map((m) => dndId.meal(m.id))} strategy={verticalListSortingStrategy}>
        <ul className="flex flex-col gap-1.5">
          {meals.map((meal) => (
            <SortableMealCard
              key={meal.id}
              meal={meal}
              cellDisabled={disabled}
              onOpen={() => onOpenMeal(meal)}
            />
          ))}
        </ul>
      </SortableContext>

      {meals.length === 0 && !disabled && (
        <button
          type="button"
          onClick={() => onAdd(cell)}
          aria-label={`Add a recipe to ${label}`}
          className="flex h-11 items-center justify-center gap-1.5 rounded-md border border-dashed text-sm text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
        >
          <Plus className="size-4" aria-hidden /> Add
        </button>
      )}
    </section>
  )
}

function SortableMealCard({
  meal,
  cellDisabled,
  onOpen,
}: {
  meal: PlannedMeal
  cellDisabled: boolean
  onOpen: () => void
}) {
  const pending = isPending(meal)
  const data: DragData & { disabled: boolean } = { type: 'meal', meal, disabled: cellDisabled }
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } =
    useSortable({ id: dndId.meal(meal.id), data, disabled: pending })

  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Translate.toString(transform), transition }}
      className={cn(isDragging && 'opacity-40')}
    >
      <MealCardView
        meal={meal}
        onOpen={pending ? undefined : onOpen}
        handleProps={{
          ref: setActivatorNodeRef,
          ...attributes,
          ...listeners,
          disabled: pending,
          'aria-label': `Move ${meal.recipe.name}`,
        }}
      />
    </li>
  )
}

const STATUS_LABEL: Record<PlannedMeal['status'], string> = {
  planned: 'planned',
  cooked: 'cooked',
  skipped: 'skipped',
}

export function MealCardView({
  meal,
  onOpen,
  handleProps,
  overlay = false,
}: {
  meal: PlannedMeal
  onOpen?: () => void
  handleProps?: ComponentProps<'button'>
  overlay?: boolean
}) {
  const { status } = meal
  return (
    <div
      data-status={status}
      className={cn(
        'flex items-center gap-1 rounded-md border bg-card text-card-foreground',
        status === 'cooked' && 'border-emerald-600/30 bg-emerald-50 dark:bg-emerald-950/40',
        status === 'skipped' && 'bg-muted/50 text-muted-foreground',
        isPending(meal) && 'opacity-60',
        overlay && 'shadow-lg ring-2 ring-primary',
      )}
    >
      <button
        type="button"
        onClick={onOpen}
        disabled={!onOpen}
        aria-label={`${meal.recipe.name}, ${meal.servings} ${meal.servings === 1 ? 'serving' : 'servings'}, ${STATUS_LABEL[status]}`}
        className="flex min-h-11 min-w-0 flex-1 items-center gap-2 rounded-md p-1 text-left focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        <RecipePhoto
          src={meal.recipe.photo_thumb_url}
          alt={meal.recipe.name}
          className="size-9 shrink-0 rounded"
        />
        <span
          className={cn(
            'min-w-0 flex-1 truncate text-sm font-medium',
            status === 'skipped' && 'line-through',
          )}
        >
          {meal.recipe.name}
        </span>
        {status === 'cooked' && (
          <Check className="size-4 shrink-0 text-emerald-700 dark:text-emerald-400" aria-hidden />
        )}
        <span className="inline-flex shrink-0 items-center gap-0.5 rounded-full bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground tabular-nums">
          <Users className="size-3" aria-hidden />
          {meal.servings}
        </span>
      </button>
      <DragHandle {...handleProps} />
    </div>
  )
}

/** The only element with touch-action: none, so the rest of the page keeps scrolling. */
export function DragHandle({ className, ...props }: ComponentProps<'button'>) {
  return (
    <button
      type="button"
      data-drag-handle=""
      className={cn(
        'inline-flex size-11 shrink-0 cursor-grab touch-none items-center justify-center rounded-md text-muted-foreground select-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none active:cursor-grabbing disabled:cursor-default disabled:opacity-40 [-webkit-touch-callout:none]',
        className,
      )}
      {...props}
    >
      <GripVertical className="size-5" aria-hidden />
    </button>
  )
}
