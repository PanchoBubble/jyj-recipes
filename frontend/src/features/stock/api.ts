import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { ingredientKeys, type Dimension, type Ingredient } from '@/features/ingredients/api'
import { applyDelta, toMilli } from '@/features/stock/quantity'
import { ApiError, api } from '@/lib/api'

export type StockReason = 'manual' | 'cooked' | 'purchased' | 'correction' | 'undo'
export type StockSource = 'ui' | 'chat'

export interface StockItem {
  ingredient_id: number
  ingredient_name: string
  dimension: Dimension
  quantity_base: string
  base_unit: string
  display: { amount: string; unit: string }
  updated_at: string
}

export interface StockMovement {
  id: number
  ingredient_id: number
  delta_base: string
  shortfall_base: string
  reason: StockReason
  source: StockSource
  planned_meal_id: number | null
  shopping_list_id: number | null
  user_id: number
  created_at: string
}

export interface StockMovementPage {
  items: StockMovement[]
  total: number
  limit: number
  offset: number
}

export interface StockChange {
  stock: StockItem
  movement: StockMovement | null
}

export type StockUpdate =
  | { delta: string; unit: string; reason?: 'manual' | 'purchased' | 'correction' }
  | { set_to: string; unit: string; reason?: 'manual' | 'purchased' | 'correction' }

export const stockKeys = {
  all: ['stock'] as const,
  list: () => [...stockKeys.all, 'list'] as const,
  movements: (ingredientId?: number) =>
    [...stockKeys.all, 'movements', { ingredientId: ingredientId ?? null }] as const,
}

export function useStock() {
  return useQuery({
    queryKey: stockKeys.list(),
    queryFn: ({ signal }) => api.get<StockItem[]>('/stock', signal),
  })
}

export function useStockMovements(ingredientId: number, limit = 20) {
  return useQuery({
    queryKey: [...stockKeys.movements(ingredientId), limit],
    queryFn: ({ signal }) =>
      api.get<StockMovementPage>(
        `/stock/movements?ingredient_id=${ingredientId}&limit=${limit}`,
        signal,
      ),
  })
}

const updateStock = (ingredientId: number, body: StockUpdate) =>
  api.patch<StockChange>(`/stock/${ingredientId}`, body)

function patchIngredientStock(
  queryClient: QueryClient,
  ingredientId: number,
  update: (ingredient: Ingredient) => Ingredient['stock'],
) {
  queryClient.setQueryData<Ingredient[]>(ingredientKeys.lists(), (list) =>
    list?.map((i) => (i.id === ingredientId ? { ...i, stock: update(i) } : i)),
  )
}

function applyServerStock(queryClient: QueryClient, change: StockChange) {
  const { quantity_base, base_unit, display } = change.stock
  patchIngredientStock(queryClient, change.stock.ingredient_id, () => ({
    quantity_base,
    base_unit,
    display,
  }))
}

function invalidateStock(queryClient: QueryClient) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: stockKeys.all }),
    queryClient.invalidateQueries({ queryKey: ingredientKeys.all }),
  ])
}

export function stockErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return "Can't reach the server."
    return error.detail ?? error.title
  }
  return 'Something went wrong.'
}

const adjustKey = ['stock', 'adjust'] as const

interface AdjustInput {
  ingredient: Ingredient
  delta: string
  unit: string
}

/**
 * Quick +/- with the balance updated before the server answers. `delta` must be in the
 * ingredient's base unit so the optimistic value is exact; the server response replaces it.
 */
export function useAdjustStock() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: adjustKey,
    mutationFn: ({ ingredient, delta, unit }: AdjustInput) =>
      updateStock(ingredient.id, { delta, unit }),
    onMutate: async ({ ingredient, delta }) => {
      await queryClient.cancelQueries({ queryKey: ingredientKeys.lists() })
      const previous = queryClient.getQueryData<Ingredient[]>(ingredientKeys.lists())
      const deltaMilli = toMilli(delta)
      patchIngredientStock(queryClient, ingredient.id, (i) =>
        applyDelta(i.stock, deltaMilli, i.dimension),
      )
      return { previous }
    },
    onError: (error, { ingredient }, context) => {
      if (context?.previous) queryClient.setQueryData(ingredientKeys.lists(), context.previous)
      toast.error(`Couldn't update ${ingredient.name}`, { description: stockErrorMessage(error) })
    },
    onSuccess: (change) => {
      // Only trust the response when no other tap is still in flight, otherwise it would
      // briefly undo the optimistic value of the later taps.
      if (queryClient.isMutating({ mutationKey: adjustKey }) === 1) {
        applyServerStock(queryClient, change)
      }
    },
    onSettled: () => {
      if (queryClient.isMutating({ mutationKey: adjustKey }) === 1) {
        return invalidateStock(queryClient)
      }
    },
  })
}

export function useSetStock(ingredientId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ amount, unit }: { amount: string; unit: string }) =>
      updateStock(ingredientId, { set_to: amount, unit }),
    onSuccess: (change) => {
      applyServerStock(queryClient, change)
      return invalidateStock(queryClient)
    },
  })
}
