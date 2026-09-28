import { useDroppable } from '@dnd-kit/core'
import { SortableContext, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Check, Plus, Users } from 'lucide-react'
import type { ComponentProps } from 'react'

import { RecipePhoto } from '@/features/recipes/RecipePhoto'
import { cn } from '@/lib/utils'

import { formatLongDay, type IsoDate } from './dates'
import { DragHandle } from './DragHandle'
import { dndId, isPending, type DragData, type DropData, type PlannedMeal } from './plan'

interface DayColumnProps {
  date: IsoDate
  isToday: boolean
  meals: PlannedMeal[]
  onOpenMeal: (meal: PlannedMeal) => void
  onAdd: (date: IsoDate) => void
}

/** One day: a single drop zone listing its meals in order, with the empty rest tappable to add. */
export function DayColumn({ date, isToday, meals, onOpenMeal, onAdd }: DayColumnProps) {
  const data: DropData = { type: 'day', date }
  const { setNodeRef, isOver, active } = useDroppable({ id: dndId.day(date), data })
  const label = formatLongDay(date)

  return (
    <section
      ref={setNodeRef}
      aria-label={label}
      data-day-column={date}
      className={cn(
        'flex min-w-0 snap-start flex-col gap-1 border-r border-dashed p-1 transition-colors last:border-r-0',
        isToday && 'bg-primary/5',
        active && 'outline-1 -outline-offset-2 outline-border outline-dashed',
        isOver && 'bg-primary/10 outline-primary outline-solid',
      )}
    >
      <SortableContext items={meals.map((m) => dndId.meal(m.id))} strategy={verticalListSortingStrategy}>
        <ul className="flex flex-col gap-1">
          {meals.map((meal) => (
            <SortableMealCard key={meal.id} meal={meal} onOpen={() => onOpenMeal(meal)} />
          ))}
        </ul>
      </SortableContext>

      <button
        type="button"
        onClick={() => onAdd(date)}
        aria-label={`Add a recipe to ${label}`}
        className={cn(
          'flex min-h-11 flex-1 items-start justify-center gap-1 rounded-md pt-2.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none',
          meals.length === 0 && 'border border-dashed',
        )}
      >
        <Plus className="size-4" aria-hidden />
        {meals.length === 0 && 'Add'}
      </button>
    </section>
  )
}

function SortableMealCard({ meal, onOpen }: { meal: PlannedMeal; onOpen: () => void }) {
  const pending = isPending(meal)
  const data: DragData = { type: 'meal', meal }
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
        'relative rounded-md border bg-card text-card-foreground',
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
        aria-label={`${meal.recipe.name}${meal.slot ? `, ${meal.slot.name}` : ''}, ${meal.servings} ${meal.servings === 1 ? 'serving' : 'servings'}, ${STATUS_LABEL[status]}`}
        className="flex w-full min-w-0 flex-col items-start gap-1 rounded-md p-1 text-left focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        <span className="flex h-9 items-center md:h-8">
          <RecipePhoto
            src={meal.recipe.photo_thumb_url}
            alt={meal.recipe.name}
            className="size-9 shrink-0 rounded md:size-8"
          />
        </span>
        <span
          className={cn(
            'line-clamp-2 w-full text-xs leading-4 font-medium break-words',
            status === 'skipped' && 'line-through',
          )}
        >
          {meal.recipe.name}
        </span>
        <span className="flex flex-wrap items-center gap-1">
          {meal.slot && (
            <span
              data-meal-label=""
              className="max-w-full truncate rounded-full bg-primary/10 px-1.5 py-0.5 text-[0.6875rem] leading-none font-medium text-primary"
            >
              {meal.slot.name}
            </span>
          )}
          <span className="inline-flex shrink-0 items-center gap-0.5 rounded-full bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground tabular-nums">
            <Users className="size-3" aria-hidden />
            {meal.servings}
          </span>
          {status === 'cooked' && (
            <Check className="size-4 shrink-0 text-emerald-700 dark:text-emerald-400" aria-hidden />
          )}
        </span>
      </button>
      <DragHandle {...handleProps} className="absolute top-0 right-0 md:size-9" />
    </div>
  )
}
