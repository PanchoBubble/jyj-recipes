import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { ingredientKeys } from '@/features/ingredients/api'
import { shoppingKeys } from '@/features/shopping/api'
import { stockKeys } from '@/features/stock/api'
import { ApiError, api } from '@/lib/api'

import { toastCookResult, type CookResult } from './cook'

import type { IsoDate } from './dates'
import {
  applyInsert,
  applyMove,
  applyRemove,
  applyReplace,
  draftMeal,
  type MealSlot,
  type PlannedMeal,
  type TrayRecipe,
} from './plan'

export const mealSlotKeys = {
  all: ['meal-slots'] as const,
}

export const plannedMealKeys = {
  all: ['planned-meals'] as const,
  ranges: () => [...plannedMealKeys.all, 'range'] as const,
  range: (from: IsoDate, to: IsoDate) => [...plannedMealKeys.ranges(), { from, to }] as const,
}

export function useMealSlots() {
  return useQuery({
    queryKey: mealSlotKeys.all,
    queryFn: async ({ signal }) => {
      const slots = await api.get<MealSlot[]>('/meal-slots', signal)
      return [...slots].sort((a, b) => a.position - b.position || a.id - b.id)
    },
  })
}

export function usePlannedMeals(from: IsoDate, to: IsoDate) {
  return useQuery({
    queryKey: plannedMealKeys.range(from, to),
    queryFn: ({ signal }) =>
      api.get<PlannedMeal[]>(`/planned-meals?${new URLSearchParams({ from, to })}`, signal),
  })
}

export function mealErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return "Can't reach the server. Check your connection."
    return error.detail ?? error.title
  }
  return 'Something went wrong. Try again.'
}

// Every calendar write shares this key so a settling mutation only refetches once
// no other one is in flight; otherwise the refetch would clobber a newer optimistic state.
const mutationKey = [...plannedMealKeys.all, 'write'] as const

type Snapshot = [readonly unknown[], PlannedMeal[] | undefined][]

async function optimistic(
  queryClient: QueryClient,
  update: (meals: PlannedMeal[]) => PlannedMeal[],
): Promise<Snapshot> {
  await queryClient.cancelQueries({ queryKey: plannedMealKeys.ranges() })
  const snapshot = queryClient.getQueriesData<PlannedMeal[]>({ queryKey: plannedMealKeys.ranges() })
  queryClient.setQueriesData<PlannedMeal[]>(
    { queryKey: plannedMealKeys.ranges() },
    (old) => old && update(old),
  )
  return snapshot
}

function rollback(queryClient: QueryClient, snapshot: Snapshot | undefined) {
  for (const [key, data] of snapshot ?? []) queryClient.setQueryData(key, data)
}

function patchCaches(queryClient: QueryClient, update: (meals: PlannedMeal[]) => PlannedMeal[]) {
  queryClient.setQueriesData<PlannedMeal[]>(
    { queryKey: plannedMealKeys.ranges() },
    (old) => old && update(old),
  )
}

function settle(queryClient: QueryClient) {
  if (queryClient.isMutating({ mutationKey }) > 1) return
  return queryClient.invalidateQueries({ queryKey: plannedMealKeys.ranges() })
}

export interface CreateMealInput {
  date: IsoDate
  recipe: TrayRecipe
  servings: number
  /** Index in the day; the server appends when it is left out. */
  position?: number
}

export function useCreatePlannedMeal() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey,
    mutationFn: ({ date, recipe, servings, position }: CreateMealInput) =>
      api.post<PlannedMeal>('/planned-meals', {
        date,
        recipe_id: recipe.id,
        servings,
        ...(position === undefined ? {} : { position }),
      }),
    onMutate: async ({ date, recipe, servings, position }) => {
      const draft = draftMeal(recipe, date, servings)
      const snapshot = await optimistic(queryClient, (meals) => applyInsert(meals, draft, position))
      return { snapshot, tempId: draft.id }
    },
    onSuccess: (saved, _input, context) => {
      patchCaches(queryClient, (meals) => applyReplace(meals, context.tempId, saved))
    },
    onError: (error, { recipe }, context) => {
      rollback(queryClient, context?.snapshot)
      toast.error(`Couldn't add ${recipe.name}`, { description: mealErrorMessage(error) })
    },
    onSettled: () => settle(queryClient),
  })
}

export interface MealChanges {
  date?: IsoDate
  position?: number
  servings?: number
  status?: 'planned' | 'skipped'
  /** null clears the label; leaving it out keeps it. */
  slot_id?: number | null
}

export interface UpdateMealInput {
  meal: PlannedMeal
  changes: MealChanges
  /** The label behind `changes.slot_id`, so the optimistic card shows it. */
  slot?: MealSlot | null
}

export function useUpdatePlannedMeal() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey,
    mutationFn: ({ meal, changes }: UpdateMealInput) =>
      api.patch<PlannedMeal>(`/planned-meals/${meal.id}`, changes),
    onMutate: async ({ meal, changes, slot }) => {
      const { date, position, ...fields } = changes
      const label = 'slot_id' in changes ? { slot: changes.slot_id === null ? null : (slot ?? meal.slot) } : {}
      const moves = date !== undefined || position !== undefined
      const snapshot = await optimistic(queryClient, (meals) => {
        const next = moves ? applyMove(meals, meal.id, date ?? meal.date, position) : meals
        return next.map((m) => (m.id === meal.id ? { ...m, ...fields, ...label } : m))
      })
      return { snapshot }
    },
    onSuccess: (saved) => {
      patchCaches(queryClient, (meals) => applyReplace(meals, saved.id, saved))
    },
    onError: (error, { meal }, context) => {
      rollback(queryClient, context?.snapshot)
      toast.error(`Couldn't update ${meal.recipe.name}`, { description: mealErrorMessage(error) })
    },
    onSettled: () => settle(queryClient),
  })
}

export function useDeletePlannedMeal() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey,
    mutationFn: (meal: PlannedMeal) => api.delete<void>(`/planned-meals/${meal.id}`),
    onMutate: async (meal) => ({
      snapshot: await optimistic(queryClient, (meals) => applyRemove(meals, meal.id)),
    }),
    onError: (error, meal, context) => {
      rollback(queryClient, context?.snapshot)
      toast.error(`Couldn't remove ${meal.recipe.name}`, { description: mealErrorMessage(error) })
    },
    onSettled: () => settle(queryClient),
  })
}

export type CookAction = 'cook' | 'uncook'

/**
 * Cooking writes stock movements, so stock, ingredient levels and shopping previews
 * go stale along with the week view.
 */
export function useCookPlannedMeal() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey,
    mutationFn: ({ meal, action }: { meal: PlannedMeal; action: CookAction }) =>
      api.post<CookResult>(`/planned-meals/${meal.id}/${action}`),
    onMutate: async ({ meal, action }) => {
      const status = action === 'cook' ? 'cooked' : 'planned'
      const snapshot = await optimistic(queryClient, (meals) =>
        meals.map((m) => (m.id === meal.id ? { ...m, status } : m)),
      )
      return { snapshot }
    },
    onSuccess: (result, { action }) => {
      patchCaches(queryClient, (meals) => applyReplace(meals, result.meal.id, result.meal))
      toastCookResult(result, action)
    },
    onError: (error, { meal, action }, context) => {
      rollback(queryClient, context?.snapshot)
      const verb = action === 'cook' ? 'mark' : 'undo'
      toast.error(`Couldn't ${verb} ${meal.recipe.name}${action === 'cook' ? ' as cooked' : ''}`, {
        description: mealErrorMessage(error),
      })
    },
    onSettled: () =>
      Promise.all([
        settle(queryClient),
        queryClient.invalidateQueries({ queryKey: stockKeys.all }),
        queryClient.invalidateQueries({ queryKey: ingredientKeys.all }),
        queryClient.invalidateQueries({ queryKey: shoppingKeys.previews() }),
      ]),
  })
}
