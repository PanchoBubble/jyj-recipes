import type { RecipeSummary } from '@/features/recipes/api'

import type { IsoDate } from './dates'

export type PlannedMealStatus = 'planned' | 'cooked' | 'skipped'

export interface MealSlot {
  id: number
  name: string
  position: number
  active: boolean
}

export interface PlannedRecipe {
  id: number
  name: string
  photo_url: string | null
  photo_thumb_url: string | null
  default_servings: number
  archived_at: string | null
}

export interface PlannedMeal {
  id: number
  date: IsoDate
  slot_id: number
  recipe_id: number
  servings: number
  position: number
  status: PlannedMealStatus
  cooked_at: string | null
  cooked_by: number | null
  created_by: number
  created_at: string
  updated_at: string
  recipe: PlannedRecipe
  slot: MealSlot
}

export interface Cell {
  date: IsoDate
  slotId: number
}

export function sameCell(meal: Pick<PlannedMeal, 'date' | 'slot_id'>, cell: Cell) {
  return meal.date === cell.date && meal.slot_id === cell.slotId
}

function byPosition(a: PlannedMeal, b: PlannedMeal) {
  return a.position - b.position || a.id - b.id
}

/** Meals of one cell in display order, matching the server's (position, id) ordering. */
export function cellMeals(meals: readonly PlannedMeal[], cell: Cell): PlannedMeal[] {
  return meals.filter((m) => sameCell(m, cell)).sort(byPosition)
}

function renumber(meals: PlannedMeal[]): PlannedMeal[] {
  return meals.map((m, position) => (m.position === position ? m : { ...m, position }))
}

function replaceCells(
  meals: readonly PlannedMeal[],
  cells: Cell[],
  next: PlannedMeal[],
): PlannedMeal[] {
  const untouched = meals.filter((m) => !cells.some((c) => sameCell(m, c)))
  return [...untouched, ...next]
}

/** Mirrors the server: the meal leaves its cell and is inserted at `position` in the target. */
export function applyMove(
  meals: readonly PlannedMeal[],
  id: number,
  target: Cell,
  position: number | undefined,
  slot?: MealSlot,
): PlannedMeal[] {
  const meal = meals.find((m) => m.id === id)
  if (!meal) return [...meals]
  const source: Cell = { date: meal.date, slotId: meal.slot_id }
  const moved: PlannedMeal = {
    ...meal,
    date: target.date,
    slot_id: target.slotId,
    slot: slot ?? meal.slot,
  }
  const targetList = cellMeals(meals, target).filter((m) => m.id !== id)
  const index = Math.min(position ?? targetList.length, targetList.length)
  targetList.splice(index, 0, moved)
  if (sameCell(meal, target)) return replaceCells(meals, [target], renumber(targetList))
  const sourceList = cellMeals(meals, source).filter((m) => m.id !== id)
  return replaceCells(meals, [source, target], [...renumber(sourceList), ...renumber(targetList)])
}

export function applyAppend(meals: readonly PlannedMeal[], meal: PlannedMeal): PlannedMeal[] {
  const cell = { date: meal.date, slotId: meal.slot_id }
  return [...meals, { ...meal, position: cellMeals(meals, cell).length }]
}

export function applyRemove(meals: readonly PlannedMeal[], id: number): PlannedMeal[] {
  const meal = meals.find((m) => m.id === id)
  if (!meal) return [...meals]
  const cell = { date: meal.date, slotId: meal.slot_id }
  return replaceCells(meals, [cell], renumber(cellMeals(meals, cell).filter((m) => m.id !== id)))
}

export function applyReplace(
  meals: readonly PlannedMeal[],
  id: number,
  meal: PlannedMeal,
): PlannedMeal[] {
  return meals.map((m) => (m.id === id ? meal : m))
}

let tempId = 0
/** Negative ids mark meals the server has not confirmed yet. */
export function nextTempId() {
  tempId -= 1
  return tempId
}

export function isPending(meal: Pick<PlannedMeal, 'id'>) {
  return meal.id < 0
}

export function draftMeal(
  recipe: Pick<RecipeSummary, 'id' | 'name' | 'photo_url' | 'photo_thumb_url' | 'default_servings'>,
  cell: Cell,
  slot: MealSlot,
  servings: number,
): PlannedMeal {
  const now = new Date().toISOString()
  return {
    id: nextTempId(),
    date: cell.date,
    slot_id: cell.slotId,
    recipe_id: recipe.id,
    servings,
    position: 0,
    status: 'planned',
    cooked_at: null,
    cooked_by: null,
    created_by: 0,
    created_at: now,
    updated_at: now,
    recipe: {
      id: recipe.id,
      name: recipe.name,
      photo_url: recipe.photo_url,
      photo_thumb_url: recipe.photo_thumb_url,
      default_servings: recipe.default_servings,
      archived_at: null,
    },
    slot,
  }
}

// Drag and drop ---------------------------------------------------------------

export type TrayRecipe = Pick<
  RecipeSummary,
  'id' | 'name' | 'photo_url' | 'photo_thumb_url' | 'default_servings'
>

export type DragData =
  | { type: 'recipe'; recipe: TrayRecipe }
  | { type: 'meal'; meal: PlannedMeal; disabled?: boolean }

export type DropData =
  | { type: 'cell'; cell: Cell; disabled?: boolean }
  | { type: 'meal'; meal: PlannedMeal; disabled?: boolean }
  | { type: 'tray' }

export type DropAction =
  | { kind: 'create'; cell: Cell; recipe: TrayRecipe; servings: number }
  | { kind: 'move'; id: number; cell: Cell; position: number }
  | { kind: 'reorder'; id: number; cell: Cell; position: number }

export const dndId = {
  recipe: (id: number) => `recipe:${id}`,
  meal: (id: number) => `meal:${id}`,
  cell: (cell: Cell) => `cell:${cell.date}:${cell.slotId}`,
  tray: 'tray',
}

/** Turns a finished drag into the API call it stands for, or null when nothing should change. */
export function resolveDragEnd(
  active: DragData | undefined,
  over: DropData | undefined,
  meals: readonly PlannedMeal[],
): DropAction | null {
  if (!active || !over || over.type === 'tray' || over.disabled) return null

  const cell: Cell =
    over.type === 'cell' ? over.cell : { date: over.meal.date, slotId: over.meal.slot_id }
  const list = cellMeals(meals, cell)

  if (active.type === 'recipe') {
    return { kind: 'create', cell, recipe: active.recipe, servings: active.recipe.default_servings }
  }

  const meal = active.meal
  if (sameCell(meal, cell)) {
    const from = list.findIndex((m) => m.id === meal.id)
    const to =
      over.type === 'meal' ? list.findIndex((m) => m.id === over.meal.id) : list.length - 1
    if (from < 0 || to < 0 || from === to) return null
    return { kind: 'reorder', id: meal.id, cell, position: to }
  }

  const others = list.filter((m) => m.id !== meal.id)
  const position =
    over.type === 'meal' ? Math.max(0, others.findIndex((m) => m.id === over.meal.id)) : others.length
  return { kind: 'move', id: meal.id, cell, position }
}
