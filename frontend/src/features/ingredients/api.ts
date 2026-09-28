import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/lib/api'

export type Dimension = 'mass' | 'volume' | 'count' | 'none'
export type MeasurableDimension = Exclude<Dimension, 'none'>

export interface Unit {
  code: string
  dimension: Dimension
  to_base: string | null
}

export interface StockLevel {
  quantity_base: string
  base_unit: string
  display: { amount: string; unit: string }
}

export interface Ingredient {
  id: number
  name: string
  dimension: Dimension
  default_unit: string
  category: string | null
  grams_per_ml: string | null
  grams_per_piece: string | null
  stock: StockLevel
  created_at: string
  updated_at: string
}

export interface IngredientInput {
  name: string
  dimension: MeasurableDimension
  default_unit: string
  category: string | null
  grams_per_ml: string | null
  grams_per_piece: string | null
}

export const unitKeys = {
  all: ['units'] as const,
}

export const ingredientKeys = {
  all: ['ingredients'] as const,
  lists: () => [...ingredientKeys.all, 'list'] as const,
  detail: (id: number) => [...ingredientKeys.all, 'detail', id] as const,
}

export function useUnits() {
  return useQuery({
    queryKey: unitKeys.all,
    queryFn: ({ signal }) => api.get<Unit[]>('/units', signal),
    staleTime: Infinity,
  })
}

/** Full list; search and category filtering happen client-side so typing never waits on the network. */
export function useIngredients() {
  return useQuery({
    queryKey: ingredientKeys.lists(),
    queryFn: ({ signal }) => api.get<Ingredient[]>('/ingredients', signal),
  })
}

export function useCreateIngredient() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: IngredientInput) => api.post<Ingredient>('/ingredients', input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ingredientKeys.all }),
  })
}

export function useUpdateIngredient(id: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: Partial<IngredientInput>) =>
      api.patch<Ingredient>(`/ingredients/${id}`, input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ingredientKeys.all }),
  })
}

export function useDeleteIngredient(id: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.delete<void>(`/ingredients/${id}`),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: ingredientKeys.detail(id) })
      return queryClient.invalidateQueries({ queryKey: ingredientKeys.all })
    },
  })
}

/** Units an amount can be entered in for this ingredient, following the backend's conversion rules. */
export function convertibleUnits(units: Unit[], ingredient: Ingredient): Unit[] {
  const perMl = ingredient.grams_per_ml !== null
  const perPiece = ingredient.grams_per_piece !== null
  const reaches = (dim: Dimension) => {
    if (dim === 'none') return false
    if (dim === ingredient.dimension) return true
    const pair = new Set([dim, ingredient.dimension])
    if (pair.has('mass') && pair.has('volume')) return perMl
    if (pair.has('mass') && pair.has('count')) return perPiece
    return perMl && perPiece
  }
  return units.filter((u) => reaches(u.dimension))
}
