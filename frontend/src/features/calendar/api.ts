import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { ApiError, api } from '@/lib/api'

import type { IsoDate } from './dates'
import {
  applyAppend,
  applyMove,
  applyRemove,
  applyReplace,
  draftMeal,
  type Cell,
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
  cell: Cell
  slot: MealSlot
  recipe: TrayRecipe
  servings: number
}

export function useCreatePlannedMeal() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey,
    mutationFn: ({ cell, recipe, servings }: CreateMealInput) =>
      api.post<PlannedMeal>('/planned-meals', {
        date: cell.date,
        slot_id: cell.slotId,
        recipe_id: recipe.id,
        servings,
      }),
    onMutate: async ({ cell, slot, recipe, servings }) => {
      const draft = draftMeal(recipe, cell, slot, servings)
      const snapshot = await optimistic(queryClient, (meals) => applyAppend(meals, draft))
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
  slot_id?: number
  position?: number
  servings?: number
  status?: 'planned' | 'skipped'
}

export interface UpdateMealInput {
  meal: PlannedMeal
  changes: MealChanges
  /** Target slot for a move, so the optimistic card shows the right slot. */
  slot?: MealSlot
}

export function useUpdatePlannedMeal() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey,
    mutationFn: ({ meal, changes }: UpdateMealInput) =>
      api.patch<PlannedMeal>(`/planned-meals/${meal.id}`, changes),
    onMutate: async ({ meal, changes, slot }) => {
      const { date, slot_id, position, ...fields } = changes
      const moves = date !== undefined || slot_id !== undefined || position !== undefined
      const snapshot = await optimistic(queryClient, (meals) => {
        const next = moves
          ? applyMove(
              meals,
              meal.id,
              { date: date ?? meal.date, slotId: slot_id ?? meal.slot_id },
              position,
              slot,
            )
          : meals
        return next.map((m) => (m.id === meal.id ? { ...m, ...fields } : m))
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
