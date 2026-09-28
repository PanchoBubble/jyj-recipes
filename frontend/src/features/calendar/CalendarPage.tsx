import { DndContext, DragOverlay, type DragEndEvent, type DragStartEvent } from '@dnd-kit/core'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useMemo, useRef, useState, type TouchEvent } from 'react'
import { useSearchParams } from 'react-router'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

import { mealErrorMessage, useCreatePlannedMeal, useMealSlots, usePlannedMeals } from './api'
import { addDays, formatDay, formatWeekRange, isIsoDate, startOfWeek, todayIso, weekDays, type IsoDate } from './dates'
import {
  calendarAnnouncements,
  calendarAutoScroll,
  calendarCollision,
  calendarMeasuring,
  useCalendarSensors,
  useDropAction,
} from './dnd'
import { MealSheet } from './MealSheet'
import { RecipePicker } from './RecipePicker'
import { RecipeTray, TrayRecipeView } from './RecipeTray'
import { MealCardView, SlotCell } from './SlotCell'
import {
  cellMeals,
  resolveDragEnd,
  type Cell,
  type DragData,
  type DropData,
  type MealSlot,
  type PlannedMeal,
} from './plan'

const SWIPE_MIN_PX = 70

export function CalendarPage() {
  const [params, setParams] = useSearchParams()
  const today = todayIso()
  const weekParam = params.get('week')
  const monday = startOfWeek(isIsoDate(weekParam) ? weekParam : today)
  const thisMonday = startOfWeek(today)
  const days = useMemo(() => weekDays(monday), [monday])

  const slotsQuery = useMealSlots()
  const mealsQuery = usePlannedMeals(days[0], days[6])
  const slots = useMemo(() => slotsQuery.data ?? [], [slotsQuery.data])
  const meals = useMemo(() => mealsQuery.data ?? [], [mealsQuery.data])

  const [trayOpen, setTrayOpen] = useState(false)
  const [dragging, setDragging] = useState<DragData | null>(null)
  const [openMealId, setOpenMealId] = useState<number | null>(null)
  const [adding, setAdding] = useState<{ cell: Cell; slot: MealSlot } | null>(null)

  const sensors = useCalendarSensors()
  const announcements = useMemo(() => calendarAnnouncements(slots), [slots])
  const drop = useDropAction(slots)
  const create = useCreatePlannedMeal()

  const goToWeek = (next: IsoDate) =>
    setParams(next === thisMonday ? {} : { week: next }, { replace: true })

  const swipe = useRef<{ x: number; y: number } | null>(null)
  const onTouchStart = (event: TouchEvent) => {
    const touch = event.touches[0]
    const onHandle = (event.target as Element).closest('[data-drag-handle]')
    swipe.current = event.touches.length === 1 && !onHandle ? { x: touch.clientX, y: touch.clientY } : null
  }
  const onTouchEnd = (event: TouchEvent) => {
    const start = swipe.current
    swipe.current = null
    const touch = event.changedTouches[0]
    if (!start || !touch || dragging) return
    const dx = touch.clientX - start.x
    const dy = touch.clientY - start.y
    if (Math.abs(dx) < SWIPE_MIN_PX || Math.abs(dx) < Math.abs(dy) * 2) return
    goToWeek(addDays(monday, dx < 0 ? 7 : -7))
  }

  const onDragStart = ({ active }: DragStartEvent) => {
    setDragging((active.data.current as DragData | undefined) ?? null)
  }
  const onDragEnd = ({ active, over }: DragEndEvent) => {
    setDragging(null)
    const action = resolveDragEnd(
      active.data.current as DragData | undefined,
      over?.data.current as DropData | undefined,
      meals,
    )
    if (action) drop(action, meals)
  }

  const openMeal = meals.find((m) => m.id === openMealId) ?? null
  const loading = slotsQuery.isPending || mealsQuery.isPending
  const error = slotsQuery.error ?? mealsQuery.error
  const activeSlots = slots.filter((s) => s.active)

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={calendarCollision}
      measuring={calendarMeasuring}
      autoScroll={calendarAutoScroll}
      accessibility={{ announcements }}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onDragCancel={() => setDragging(null)}
    >
      <div className="flex flex-col gap-3">
        <div className="sticky top-[calc(3.5rem+env(safe-area-inset-top))] z-[5] -mx-4 -mt-4 flex items-center gap-1 border-b bg-background/95 px-2 py-2 backdrop-blur supports-[backdrop-filter]:bg-background/80">
          <Button
            variant="ghost"
            size="icon"
            className="size-11"
            aria-label="Previous week"
            onClick={() => goToWeek(addDays(monday, -7))}
          >
            <ChevronLeft className="size-5" aria-hidden />
          </Button>
          <h2 className="flex-1 text-center text-sm font-semibold" aria-live="polite">
            {formatWeekRange(monday)}
          </h2>
          <Button
            variant="ghost"
            size="icon"
            className="size-11"
            aria-label="Next week"
            onClick={() => goToWeek(addDays(monday, 7))}
          >
            <ChevronRight className="size-5" aria-hidden />
          </Button>
          <Button
            variant="outline"
            className="h-11"
            disabled={monday === thisMonday}
            onClick={() => goToWeek(thisMonday)}
          >
            Today
          </Button>
        </div>

        <div onTouchStart={onTouchStart} onTouchEnd={onTouchEnd} data-testid="week">
          {loading ? (
            <WeekSkeleton />
          ) : error ? (
            <div className="flex flex-col items-center gap-3 py-12 text-center">
              <p className="font-medium">Couldn't load the calendar</p>
              <p className="text-sm text-muted-foreground">{mealErrorMessage(error)}</p>
              <Button
                variant="outline"
                className="h-11"
                onClick={() => {
                  void slotsQuery.refetch()
                  void mealsQuery.refetch()
                }}
              >
                Try again
              </Button>
            </div>
          ) : activeSlots.length === 0 ? (
            <p className="py-12 text-center text-sm text-muted-foreground">
              No meal slots are active. Add one in Settings to start planning.
            </p>
          ) : (
            <ol className="flex flex-col gap-3">
              {days.map((date) => (
                <DaySection
                  key={date}
                  date={date}
                  isToday={date === today}
                  slots={slots}
                  meals={meals}
                  onOpenMeal={(meal) => setOpenMealId(meal.id)}
                  onAdd={(cell, slot) => setAdding({ cell, slot })}
                />
              ))}
            </ol>
          )}
        </div>

        {/* Room for the tray so the last day can scroll above it. */}
        <div aria-hidden className={trayOpen ? 'h-[calc(8rem+32svh)]' : 'h-12'} />
      </div>

      <RecipeTray open={trayOpen} onOpenChange={setTrayOpen} folded={dragging?.type === 'recipe'} />

      <DragOverlay dropAnimation={null}>
        {dragging?.type === 'recipe' ? (
          <TrayRecipeView recipe={dragging.recipe} overlay />
        ) : dragging?.type === 'meal' ? (
          <MealCardView meal={dragging.meal} overlay />
        ) : null}
      </DragOverlay>

      <MealSheet meal={openMeal} onOpenChange={(open) => !open && setOpenMealId(null)} />
      <RecipePicker
        target={adding}
        onOpenChange={(open) => !open && setAdding(null)}
        onPick={(cell, slot, recipe) =>
          create.mutate({ cell, slot, recipe, servings: recipe.default_servings })
        }
      />
    </DndContext>
  )
}

function DaySection({
  date,
  isToday,
  slots,
  meals,
  onOpenMeal,
  onAdd,
}: {
  date: IsoDate
  isToday: boolean
  slots: MealSlot[]
  meals: PlannedMeal[]
  onOpenMeal: (meal: PlannedMeal) => void
  onAdd: (cell: Cell, slot: MealSlot) => void
}) {
  // Inactive slots only show up on days that still hold meals in them.
  const cells = slots
    .map((slot) => {
      const cell = { date, slotId: slot.id }
      return { slot, cell, meals: cellMeals(meals, cell) }
    })
    .filter(({ slot, meals }) => slot.active || meals.length > 0)

  return (
    <li
      aria-current={isToday ? 'date' : undefined}
      className={cn('rounded-xl border bg-card p-2', isToday && 'border-primary/60')}
    >
      <h2 className="flex items-center gap-2 px-1 pb-1 text-sm font-semibold">
        {formatDay(date)}
        {isToday && (
          <span className="rounded-full bg-primary px-2 py-0.5 text-xs font-medium text-primary-foreground">
            Today
          </span>
        )}
      </h2>
      <div className="flex flex-col gap-1">
        {cells.map(({ slot, cell, meals }) => (
          <SlotCell
            key={slot.id}
            cell={cell}
            slot={slot}
            meals={meals}
            onOpenMeal={onOpenMeal}
            onAdd={(c) => onAdd(c, slot)}
          />
        ))}
      </div>
    </li>
  )
}

function WeekSkeleton() {
  return (
    <div className="flex flex-col gap-3" aria-busy="true" aria-label="Loading calendar">
      {[0, 1, 2].map((i) => (
        <Skeleton key={i} className="h-40 w-full rounded-xl" />
      ))}
    </div>
  )
}
