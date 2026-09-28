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
  slot_id: number | null
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
  /** Optional label (lunch, dinner, ...); meals are ordered per day regardless of it. */
  slot: MealSlot | null
}

function byPosition(a: PlannedMeal, b: PlannedMeal) {
  return a.position - b.position || a.id - b.id
}

/** Meals of one day in display order, matching the server's (position, id) ordering. */
export function dayMeals(meals: readonly PlannedMeal[], date: IsoDate): PlannedMeal[] {
  return meals.filter((m) => m.date === date).sort(byPosition)
}

function renumber(meals: PlannedMeal[]): PlannedMeal[] {
  return meals.map((m, position) => (m.position === position ? m : { ...m, position }))
}

function replaceDays(
  meals: readonly PlannedMeal[],
  dates: IsoDate[],
  next: PlannedMeal[],
): PlannedMeal[] {
  return [...meals.filter((m) => !dates.includes(m.date)), ...next]
}

function insertAt(list: PlannedMeal[], meal: PlannedMeal, position: number | undefined) {
  list.splice(Math.min(position ?? list.length, list.length), 0, meal)
  return list
}

/** Mirrors the server: the meal leaves its day and is inserted at `position` in the target day. */
export function applyMove(
  meals: readonly PlannedMeal[],
  id: number,
  date: IsoDate,
  position: number | undefined,
): PlannedMeal[] {
  const meal = meals.find((m) => m.id === id)
  if (!meal) return [...meals]
  const target = insertAt(
    dayMeals(meals, date).filter((m) => m.id !== id),
    { ...meal, date },
    position,
  )
  if (meal.date === date) return replaceDays(meals, [date], renumber(target))
  const source = dayMeals(meals, meal.date).filter((m) => m.id !== id)
  return replaceDays(meals, [meal.date, date], [...renumber(source), ...renumber(target)])
}

/** A new meal at `position` in its day, or at the end when none is given. */
export function applyInsert(
  meals: readonly PlannedMeal[],
  meal: PlannedMeal,
  position?: number,
): PlannedMeal[] {
  return replaceDays(meals, [meal.date], renumber(insertAt(dayMeals(meals, meal.date), meal, position)))
}

export function applyRemove(meals: readonly PlannedMeal[], id: number): PlannedMeal[] {
  const meal = meals.find((m) => m.id === id)
  if (!meal) return [...meals]
  return replaceDays(
    meals,
    [meal.date],
    renumber(dayMeals(meals, meal.date).filter((m) => m.id !== id)),
  )
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
  date: IsoDate,
  servings: number,
): PlannedMeal {
  const now = new Date().toISOString()
  return {
    id: nextTempId(),
    date,
    slot_id: null,
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
    slot: null,
  }
}

// Drag and drop ---------------------------------------------------------------

export type TrayRecipe = Pick<
  RecipeSummary,
  'id' | 'name' | 'photo_url' | 'photo_thumb_url' | 'default_servings'
>

export type DragData =
  | { type: 'recipe'; recipe: TrayRecipe }
  | { type: 'meal'; meal: PlannedMeal }

export type DropData =
  | { type: 'day'; date: IsoDate }
  | { type: 'meal'; meal: PlannedMeal }
  | { type: 'tray' }

export type DropAction =
  | { kind: 'create'; date: IsoDate; position: number; recipe: TrayRecipe; servings: number }
  | { kind: 'move'; id: number; date: IsoDate; position: number }
  | { kind: 'reorder'; id: number; date: IsoDate; position: number }

export const dndId = {
  recipe: (id: number) => `recipe:${id}`,
  meal: (id: number) => `meal:${id}`,
  day: (date: IsoDate) => `day:${date}`,
  tray: 'tray',
}

/** Turns a finished drag into the API call it stands for, or null when nothing should change. */
export function resolveDragEnd(
  active: DragData | undefined,
  over: DropData | undefined,
  meals: readonly PlannedMeal[],
): DropAction | null {
  if (!active || !over || over.type === 'tray') return null

  const date = over.type === 'day' ? over.date : over.meal.date
  const list = dayMeals(meals, date)

  if (active.type === 'recipe') {
    const at = over.type === 'meal' ? list.findIndex((m) => m.id === over.meal.id) : -1
    return {
      kind: 'create',
      date,
      position: at < 0 ? list.length : at,
      recipe: active.recipe,
      servings: active.recipe.default_servings,
    }
  }

  const meal = active.meal
  if (meal.date === date) {
    const from = list.findIndex((m) => m.id === meal.id)
    const to = over.type === 'meal' ? list.findIndex((m) => m.id === over.meal.id) : list.length - 1
    if (from < 0 || to < 0 || from === to) return null
    return { kind: 'reorder', id: meal.id, date, position: to }
  }

  const others = list.filter((m) => m.id !== meal.id)
  const at = over.type === 'meal' ? others.findIndex((m) => m.id === over.meal.id) : -1
  return { kind: 'move', id: meal.id, date, position: at < 0 ? others.length : at }
}
