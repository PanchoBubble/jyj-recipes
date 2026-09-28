import { DndContext, DragOverlay, type DragEndEvent, type DragStartEvent } from '@dnd-kit/core'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { useSearchParams } from 'react-router'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

import { mealErrorMessage, useCreatePlannedMeal, useMealSlots, usePlannedMeals } from './api'
import {
  addDays,
  formatDayMonth,
  formatWeekday,
  formatWeekRange,
  isIsoDate,
  startOfWeek,
  todayIso,
  weekDays,
  type IsoDate,
} from './dates'
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

/**
 * Slot labels, then one column per day. Below md a column is a third of what the labels
 * leave, so about three days show and the rest scroll sideways; md and up fit all seven.
 */
const gridStyle = {
  '--label': '3.5rem',
  '--col': 'max(6rem, calc((100vw - var(--label)) / 3))',
} as CSSProperties
const gridCols = 'grid grid-cols-[var(--label)_repeat(7,var(--col))] md:grid-cols-[var(--label)_repeat(7,minmax(0,1fr))]'

export function CalendarPage() {
  const [params, setParams] = useSearchParams()
  const today = todayIso()
  const weekParam = params.get('week')
  const monday = startOfWeek(isIsoDate(weekParam) ? weekParam : today)
  const thisMonday = startOfWeek(today)
  const days = weekDays(monday)

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
  // Inactive slots only show up while the week still holds meals in them.
  const rows = slots.filter((s) => s.active || meals.some((m) => m.slot_id === s.id))
  const showGrid = !loading && !error && activeSlots.length > 0

  const scroller = useRef<HTMLDivElement>(null)
  const dayStrip = useRef<HTMLDivElement>(null)
  const [scrollRequest, setScrollRequest] = useState<{ behavior: ScrollBehavior }>({ behavior: 'instant' })
  const focusDay = days.includes(today) ? today : days[0]

  useLayoutEffect(() => {
    const el = scroller.current
    if (!showGrid || !el) return
    const column = el.querySelector(`[data-cell^="cell:${focusDay}:"]`)
    const label = el.querySelector('[data-slot-label]')
    if (!column) return
    const left =
      el.scrollLeft +
      column.getBoundingClientRect().left -
      el.getBoundingClientRect().left -
      (label?.getBoundingClientRect().width ?? 0)
    if (typeof el.scrollTo === 'function') el.scrollTo({ left, behavior: scrollRequest.behavior })
    else el.scrollLeft = left
  }, [showGrid, focusDay, scrollRequest])

  const syncDayStrip = () => {
    if (scroller.current && dayStrip.current) dayStrip.current.scrollLeft = scroller.current.scrollLeft
  }

  const goToToday = () => {
    goToWeek(thisMonday)
    setScrollRequest({ behavior: monday === thisMonday ? 'smooth' : 'instant' })
  }

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
        <div className="sticky top-[calc(3.5rem+env(safe-area-inset-top))] z-[5] -mx-4 -mt-4 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80">
          <div className="flex items-center gap-1 px-2 py-2">
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
          <Button variant="outline" className="h-11" onClick={goToToday}>
            Today
          </Button>
          </div>
          {showGrid && (
            <div
              ref={dayStrip}
              aria-hidden
              data-testid="day-strip"
              className={cn(gridCols, 'overflow-hidden md:px-4')}
              style={gridStyle}
            >
              <div className="sticky left-0 z-[1] bg-background" />
              {days.map((date) => (
                <DayHeader key={date} date={date} isToday={date === today} />
              ))}
            </div>
          )}
        </div>

        <div data-testid="week">
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
            <div
              ref={scroller}
              onScroll={syncDayStrip}
              data-testid="week-grid"
              className={cn(
                gridCols,
                '-mx-4 overflow-x-auto overscroll-x-contain scroll-pl-[var(--label)] pb-1 md:px-4',
                // Snapping would fight the drag auto-scroll; it resumes on drop.
                dragging ? 'snap-none' : 'snap-x snap-mandatory',
              )}
              style={gridStyle}
            >
              {rows.map((slot) => (
                <SlotRow
                  key={slot.id}
                  slot={slot}
                  days={days}
                  today={today}
                  meals={meals}
                  onOpenMeal={(meal) => setOpenMealId(meal.id)}
                  onAdd={(cell) => setAdding({ cell, slot })}
                />
              ))}
            </div>
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

function DayHeader({ date, isToday }: { date: IsoDate; isToday: boolean }) {
  return (
    <div
      data-day={date}
      aria-current={isToday ? 'date' : undefined}
      className="flex flex-col items-center px-0.5 py-1.5 text-center leading-tight"
    >
      <span
        className={cn(
          'text-xs font-semibold tracking-wide uppercase',
          isToday ? 'text-primary' : 'text-muted-foreground',
        )}
      >
        {formatWeekday(date)}
      </span>
      <span
        className={cn(
          'mt-0.5 rounded-full px-2 py-0.5 text-sm font-medium tabular-nums',
          isToday && 'bg-primary text-primary-foreground',
        )}
      >
        {formatDayMonth(date)}
      </span>
    </div>
  )
}

function SlotRow({
  slot,
  days,
  today,
  meals,
  onOpenMeal,
  onAdd,
}: {
  slot: MealSlot
  days: IsoDate[]
  today: IsoDate
  meals: PlannedMeal[]
  onOpenMeal: (meal: PlannedMeal) => void
  onAdd: (cell: Cell) => void
}) {
  return (
    <>
      <div
        data-slot-label=""
        className="sticky left-0 z-[1] flex items-start border-b bg-background py-2 pr-1 pl-2 md:pl-0"
      >
        <h3 className="text-xs font-medium tracking-wide break-words text-muted-foreground uppercase">
          {slot.name}
          {!slot.active && <span className="block normal-case">(inactive)</span>}
        </h3>
      </div>
      {days.map((date) => {
        const cell = { date, slotId: slot.id }
        return (
          <div key={date} className={cn('border-b py-1', date === today && 'bg-primary/5')}>
            <SlotCell
              cell={cell}
              slot={slot}
              meals={cellMeals(meals, cell)}
              onOpenMeal={onOpenMeal}
              onAdd={onAdd}
            />
          </div>
        )
      })}
    </>
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
