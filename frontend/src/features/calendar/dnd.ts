import {
  closestCenter,
  KeyboardSensor,
  MeasuringStrategy,
  PointerSensor,
  pointerWithin,
  TouchSensor,
  useSensor,
  useSensors,
  type Announcements,
  type CollisionDetection,
  type DndContextProps,
  type PointerSensorOptions,
} from '@dnd-kit/core'
import { sortableKeyboardCoordinates } from '@dnd-kit/sortable'
import { useCallback } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'

import { useCreatePlannedMeal, useUpdatePlannedMeal } from './api'
import { formatLongDay } from './dates'
import { dndId, type DragData, type DropAction, type DropData, type MealSlot, type PlannedMeal } from './plan'

/**
 * Mouse and pen only: touch goes through TouchSensor so a finger has to hold still
 * for a moment before a drag starts, and a quick swipe stays a scroll.
 */
class MousePenSensor extends PointerSensor {
  static activators = [
    {
      eventName: 'onPointerDown' as const,
      handler: (event: ReactPointerEvent, options: PointerSensorOptions) =>
        event.nativeEvent.pointerType !== 'touch' &&
        PointerSensor.activators[0].handler(event, options),
    },
  ]
}

export function useCalendarSensors() {
  return useSensors(
    useSensor(MousePenSensor, { activationConstraint: { distance: 5 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 200, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )
}

const isCell = (id: unknown) => String(id).startsWith('cell:')
const isMeal = (id: unknown) => String(id).startsWith('meal:')

/**
 * Most specific target under the finger (tray, then meal card, then cell). Anywhere else,
 * including gaps and headers, falls back to the nearest cell so a drop is never lost.
 */
export const calendarCollision: CollisionDetection = (args) => {
  if (args.pointerCoordinates) {
    const hits = pointerWithin(args)
    const hit =
      hits.find((h) => h.id === dndId.tray) ??
      hits.find((h) => isMeal(h.id)) ??
      hits.find((h) => isCell(h.id))
    if (hit) return [hit]
    return closestCenter({
      ...args,
      droppableContainers: args.droppableContainers.filter((c) => isCell(c.id)),
    })
  }
  return closestCenter({
    ...args,
    droppableContainers: args.droppableContainers.filter((c) => c.id !== dndId.tray),
  })
}

export const calendarMeasuring: DndContextProps['measuring'] = {
  droppable: { strategy: MeasuringStrategy.Always },
}

export const calendarAutoScroll: DndContextProps['autoScroll'] = {
  threshold: { x: 0, y: 0.15 },
  acceleration: 12,
}

export function describeDrag(data: DragData | undefined) {
  if (!data) return 'item'
  return data.type === 'recipe' ? data.recipe.name : data.meal.recipe.name
}

export function describeDrop(data: DropData | undefined, slots: MealSlot[]) {
  if (!data) return 'nowhere'
  if (data.type === 'tray') return 'the recipe tray, release to cancel'
  const cell = data.type === 'cell' ? data.cell : { date: data.meal.date, slotId: data.meal.slot_id }
  const slot = slots.find((s) => s.id === cell.slotId)
  return `${formatLongDay(cell.date)}, ${slot?.name ?? 'meal'}`
}

export function calendarAnnouncements(slots: MealSlot[]): Announcements {
  const drag = (d: unknown) => describeDrag(d as DragData | undefined)
  const drop = (d: unknown) => describeDrop(d as DropData | undefined, slots)
  return {
    onDragStart: ({ active }) => `Picked up ${drag(active.data.current)}.`,
    onDragOver: ({ active, over }) =>
      over ? `${drag(active.data.current)} is over ${drop(over.data.current)}.` : undefined,
    onDragEnd: ({ active, over }) =>
      over
        ? `${drag(active.data.current)} was dropped on ${drop(over.data.current)}.`
        : `${drag(active.data.current)} was dropped.`,
    onDragCancel: ({ active }) => `Dragging ${drag(active.data.current)} was cancelled.`,
  }
}

/** Runs the API call a drop resolved to; each mutation updates the cache optimistically. */
export function useDropAction(slots: MealSlot[]) {
  const create = useCreatePlannedMeal()
  const update = useUpdatePlannedMeal()
  const { mutate: createMeal } = create
  const { mutate: updateMeal } = update

  return useCallback(
    (action: DropAction, meals: readonly PlannedMeal[]) => {
      const slot = slots.find((s) => s.id === action.cell.slotId)
      if (!slot) return
      if (action.kind === 'create') {
        createMeal({ cell: action.cell, slot, recipe: action.recipe, servings: action.servings })
        return
      }
      const meal = meals.find((m) => m.id === action.id)
      if (!meal) return
      const changes =
        action.kind === 'move'
          ? { date: action.cell.date, slot_id: action.cell.slotId, position: action.position }
          : { position: action.position }
      updateMeal({ meal, changes, slot })
    },
    [slots, createMeal, updateMeal],
  )
}
