import { DndContext, DragOverlay, type DragEndEvent, type DragStartEvent } from '@dnd-kit/core'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { useSearchParams } from 'react-router'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { setDragging as setAppDragging } from '@/lib/dragging'
import { cn } from '@/lib/utils'

import { mealErrorMessage, useCreatePlannedMeal, usePlannedMeals } from './api'
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
import { DayColumn, MealCardView } from './DayColumn'
import {
  calendarAnnouncements,
  calendarAutoScroll,
  calendarCollision,
  calendarMeasuring,
  useCalendarSensors,
  useDropAction,
} from './dnd'
import { MealSheet } from './MealSheet'
import { RecipePanel, PanelRecipeView } from './RecipePanel'
import { RecipePicker } from './RecipePicker'
import { dayMeals, resolveDragEnd, type DragData, type DropData } from './plan'

/**
 * One column per day. Below md a column is a third of the screen, so about three days show
 * and the rest scroll sideways; md and up fit all seven.
 */
const gridStyle = { '--col': 'max(6rem, calc(min(100vw, 42rem) / 3))' } as CSSProperties
const gridCols = 'grid grid-cols-[repeat(7,var(--col))] md:grid-cols-7'

export function CalendarPage() {
  const [params, setParams] = useSearchParams()
  const today = todayIso()
  const weekParam = params.get('week')
  const monday = startOfWeek(isIsoDate(weekParam) ? weekParam : today)
  const thisMonday = startOfWeek(today)
  const days = weekDays(monday)

  const mealsQuery = usePlannedMeals(days[0], days[6])
  const meals = useMemo(() => mealsQuery.data ?? [], [mealsQuery.data])

  const [dragging, setDragging] = useState<DragData | null>(null)
  const [openMealId, setOpenMealId] = useState<number | null>(null)
  const [adding, setAdding] = useState<IsoDate | null>(null)

  const sensors = useCalendarSensors()
  const announcements = useMemo(() => calendarAnnouncements(), [])
  const drop = useDropAction()
  const create = useCreatePlannedMeal()

  useEffect(() => () => setAppDragging(false), [])

  const goToWeek = (next: IsoDate) =>
    setParams(next === thisMonday ? {} : { week: next }, { replace: true })

  const endDrag = () => {
    setDragging(null)
    setAppDragging(false)
  }
  const onDragStart = ({ active }: DragStartEvent) => {
    setDragging((active.data.current as DragData | undefined) ?? null)
    setAppDragging(true)
  }
  const onDragEnd = ({ active, over }: DragEndEvent) => {
    endDrag()
    const action = resolveDragEnd(
      active.data.current as DragData | undefined,
      over?.data.current as DropData | undefined,
      meals,
    )
    if (action) drop(action, meals)
  }

  const openMeal = meals.find((m) => m.id === openMealId) ?? null
  const loading = mealsQuery.isPending
  const error = mealsQuery.error
  const showGrid = !loading && !error

  const scroller = useRef<HTMLDivElement>(null)
  const dayStrip = useRef<HTMLDivElement>(null)
  const [scrollRequest, setScrollRequest] = useState<{ behavior: ScrollBehavior }>({ behavior: 'instant' })
  const focusDay = days.includes(today) ? today : days[0]

  useLayoutEffect(() => {
    const el = scroller.current
    if (!showGrid || !el) return
    const column = el.querySelector(`[data-day-column="${focusDay}"]`)
    if (!column) return
    const left = el.scrollLeft + column.getBoundingClientRect().left - el.getBoundingClientRect().left
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
      onDragCancel={endDrag}
    >
      {/* Pinned between the app header and the tab bar: the week and the recipes scroll on their own, the page never does. */}
      <div
        data-testid="calendar-screen"
        className="fixed inset-x-0 top-[calc(3.5rem+1px+env(safe-area-inset-top))] bottom-[calc(4rem+1px+env(safe-area-inset-bottom))] mx-auto flex max-w-2xl flex-col px-[env(safe-area-inset-left)]"
      >
        <div className="shrink-0 border-b bg-background">
          <div className="flex items-center gap-1 px-2 py-1">
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
              className={cn(gridCols, 'overflow-hidden')}
              style={gridStyle}
            >
              {days.map((date) => (
                <DayHeader key={date} date={date} isToday={date === today} />
              ))}
            </div>
          )}
        </div>

        <div data-testid="week" className="flex min-h-0 flex-[3] flex-col">
          {loading ? (
            <WeekSkeleton />
          ) : error ? (
            <div className="flex flex-col items-center gap-3 px-4 py-12 text-center">
              <p className="font-medium">Couldn't load the calendar</p>
              <p className="text-sm text-muted-foreground">{mealErrorMessage(error)}</p>
              <Button variant="outline" className="h-11" onClick={() => void mealsQuery.refetch()}>
                Try again
              </Button>
            </div>
          ) : (
            <div
              ref={scroller}
              onScroll={syncDayStrip}
              data-testid="week-grid"
              className={cn(
                'min-h-0 flex-1 overflow-auto overscroll-contain',
                // Snapping would fight the drag auto-scroll; it resumes on drop.
                dragging ? 'snap-none' : 'snap-x snap-mandatory',
              )}
            >
              {/* Bottom room so the last meal can scroll clear of the chat bubble. */}
              <div className={cn(gridCols, 'min-h-full pb-16')} style={gridStyle}>
                {days.map((date) => (
                  <DayColumn
                    key={date}
                    date={date}
                    isToday={date === today}
                    meals={dayMeals(meals, date)}
                    onOpenMeal={(meal) => setOpenMealId(meal.id)}
                    onAdd={setAdding}
                  />
                ))}
              </div>
            </div>
          )}
        </div>

        <RecipePanel className="flex-[2] md:flex-[1.5]" />
      </div>

      <DragOverlay dropAnimation={null}>
        {dragging?.type === 'recipe' ? (
          <PanelRecipeView recipe={dragging.recipe} overlay />
        ) : dragging?.type === 'meal' ? (
          <MealCardView meal={dragging.meal} overlay />
        ) : null}
      </DragOverlay>

      <MealSheet meal={openMeal} onOpenChange={(open) => !open && setOpenMealId(null)} />
      <RecipePicker
        target={adding}
        onOpenChange={(open) => !open && setAdding(null)}
        onPick={(date, recipe) => create.mutate({ date, recipe, servings: recipe.default_servings })}
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

function WeekSkeleton() {
  return (
    <div className="flex flex-col gap-3 p-4" aria-busy="true" aria-label="Loading calendar">
      {[0, 1, 2].map((i) => (
        <Skeleton key={i} className="h-24 w-full rounded-xl" />
      ))}
    </div>
  )
}
