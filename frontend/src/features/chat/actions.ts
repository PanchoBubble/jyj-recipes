import type { QueryClient, QueryKey } from '@tanstack/react-query'

import { plannedMealKeys } from '@/features/calendar/api'
import { formatDay, isIsoDate, startOfWeek } from '@/features/calendar/dates'
import { ingredientKeys } from '@/features/ingredients/api'
import { recipeKeys } from '@/features/recipes/api'
import { shoppingKeys } from '@/features/shopping/api'
import { stockKeys } from '@/features/stock/api'
import { meQueryKey } from '@/lib/query'

import { chatKeys, type ChatAction } from './api'

interface ToolLabel {
  done: string
  proposed: string
}

const LABELS: Record<string, ToolLabel> = {
  adjust_stock: { done: 'Adjusted stock', proposed: 'Adjust stock' },
  set_stock: { done: 'Set stock level', proposed: 'Set stock level' },
  create_ingredient: { done: 'Added ingredient', proposed: 'Add ingredient' },
  create_recipe: { done: 'Created recipe', proposed: 'Create recipe' },
  update_recipe: { done: 'Updated recipe', proposed: 'Update recipe' },
  delete_recipe: { done: 'Deleted recipe', proposed: 'Delete recipe' },
  plan_meal: { done: 'Planned meal', proposed: 'Plan meal' },
  move_meal: { done: 'Moved meal', proposed: 'Move meal' },
  set_servings: { done: 'Changed servings', proposed: 'Change servings' },
  remove_meal: { done: 'Removed meal', proposed: 'Remove meal' },
  mark_cooked: { done: 'Marked meal cooked', proposed: 'Mark meal cooked' },
  uncook_meal: { done: 'Marked meal not cooked', proposed: 'Mark meal not cooked' },
  create_shopping_list: { done: 'Created shopping list', proposed: 'Create shopping list' },
}

function humanize(tool: string) {
  const words = tool.replace(/_/g, ' ').trim() || 'Unknown action'
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function toolLabel(action: Pick<ChatAction, 'tool' | 'status'>): string {
  const label = LABELS[action.tool]
  if (!label) return humanize(action.tool)
  return action.status === 'executed' ? label.done : label.proposed
}

/** The backend prefixes summaries with the raw tool label ("adjust stock: Flour"). */
export function summarySubject(action: Pick<ChatAction, 'tool' | 'summary'>): string | null {
  const prefix = action.tool.replace(/_/g, ' ')
  if (action.summary === prefix) return null
  if (action.summary.startsWith(`${prefix}: `)) return action.summary.slice(prefix.length + 2)
  return action.summary || null
}

type Area = 'recipes' | 'stock' | 'ingredients' | 'calendar' | 'shopping'

const MEAL_TOOLS = new Set(['plan_meal', 'move_meal', 'set_servings', 'remove_meal'])
const COOK_TOOLS = new Set(['mark_cooked', 'uncook_meal'])

function areasFor(tool: string): Area[] | null {
  if (tool.endsWith('_stock')) return ['stock', 'ingredients', 'shopping']
  if (tool.endsWith('_ingredient')) return ['ingredients', 'stock']
  if (tool.endsWith('_recipe')) return ['recipes', 'calendar', 'shopping']
  if (MEAL_TOOLS.has(tool)) return ['calendar', 'shopping']
  if (COOK_TOOLS.has(tool)) return ['calendar', 'stock', 'ingredients', 'shopping']
  if (tool === 'create_shopping_list') return ['shopping']
  return null
}

const AREA_KEYS: Record<Area, QueryKey> = {
  recipes: recipeKeys.all,
  stock: stockKeys.all,
  ingredients: ingredientKeys.all,
  calendar: plannedMealKeys.all,
  shopping: shoppingKeys.all,
}

/** Refresh the screens a successful write can have changed. Unknown tools refresh everything. */
export function invalidateForAction(queryClient: QueryClient, tool: string) {
  const areas = areasFor(tool)
  if (!areas) {
    return queryClient.invalidateQueries({
      predicate: (q) => q.queryKey[0] !== meQueryKey[0] && q.queryKey[0] !== chatKeys.all[0],
    })
  }
  return Promise.all(areas.map((a) => queryClient.invalidateQueries({ queryKey: AREA_KEYS[a] })))
}

function numberField(data: ChatAction['data'], ...names: string[]) {
  for (const name of names) {
    const value = data?.[name]
    if (typeof value === 'number' && Number.isInteger(value)) return value
  }
  return null
}

function mealOf(data: ChatAction['data']): Record<string, unknown> | null {
  const nested = data?.meal
  if (nested && typeof nested === 'object') return nested as Record<string, unknown>
  return data ?? null
}

function mealDate(data: ChatAction['data']) {
  const value = mealOf(data)?.date
  return typeof value === 'string' && isIsoDate(value) ? value : null
}

/** What the card is about when the backend summary has no name, e.g. "Pasta · Fri 2 Oct · dinner". */
export function actionSubject(action: ChatAction): string | null {
  const subject = summarySubject(action)
  if (subject || !(MEAL_TOOLS.has(action.tool) || COOK_TOOLS.has(action.tool))) return subject
  const meal = mealOf(action.data)
  const date = mealDate(action.data)
  const parts = [meal?.recipe, date && formatDay(date), meal?.slot].filter(
    (part): part is string => typeof part === 'string' && part.length > 0,
  )
  return parts.length > 0 ? parts.join(' · ') : null
}

export interface ActionLink {
  to: string
  label: string
}

export function actionLink(action: ChatAction): ActionLink | null {
  if (action.status !== 'executed') return null
  const { tool, data } = action
  if (tool.endsWith('_recipe')) {
    const id = numberField(data, 'id', 'recipe_id')
    if (tool === 'delete_recipe' && data?.outcome !== 'archived') return null
    return id === null ? null : { to: `/recipes/${id}`, label: 'Open recipe' }
  }
  if (tool.endsWith('_stock')) return { to: '/stock', label: 'Open stock' }
  if ((MEAL_TOOLS.has(tool) && tool !== 'remove_meal') || COOK_TOOLS.has(tool)) {
    const date = mealDate(data)
    return {
      to: date ? `/calendar?week=${startOfWeek(date)}` : '/calendar',
      label: 'Open calendar',
    }
  }
  if (tool === 'create_shopping_list') {
    const id = numberField(data, 'list_id')
    return id === null
      ? { to: '/shopping', label: 'Open shopping' }
      : { to: `/shopping/lists/${id}`, label: 'Open list' }
  }
  return null
}
