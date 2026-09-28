import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'

import { ingredientKeys, type Dimension, type MeasurableDimension } from '@/features/ingredients/api'
import { ApiError, api } from '@/lib/api'

export const PAGE_SIZE = 20
export const NAME_MAX = 200
export const DESCRIPTION_MAX = 5000
export const NOTE_MAX = 200
export const SERVINGS_MAX = 100
export const LINES_MAX = 100
export const INGREDIENT_NAME_MAX = 100

export interface RecipeSummary {
  id: number
  name: string
  description: string | null
  default_servings: number
  ingredient_count: number
  photo_url: string | null
  photo_thumb_url: string | null
  created_by: number
  created_at: string
  updated_at: string
  archived_at: string | null
}

export interface RecipeIngredient {
  id: number
  position: number
  ingredient_id: number
  ingredient_name: string
  dimension: Dimension
  amount_per_person: string | null
  unit: string
  unit_dimension: Dimension
  note: string | null
}

export interface Recipe extends RecipeSummary {
  ingredients: RecipeIngredient[]
}

export interface RecipePage {
  items: RecipeSummary[]
  total: number
  page: number
  page_size: number
}

export interface ScaledIngredient {
  position: number
  ingredient_id: number
  ingredient_name: string
  amount_per_person: string | null
  unit: string
  amount: string | null
  display: { amount: string; unit: string } | null
  note: string | null
}

export interface ScaledRecipe {
  recipe_id: number
  name: string
  servings: number
  ingredients: ScaledIngredient[]
}

export interface NewIngredientInput {
  name: string
  dimension: MeasurableDimension
  default_unit: string
}

export type RecipeLineInput = (
  | { ingredient_id: number; new_ingredient?: never }
  | { new_ingredient: NewIngredientInput; ingredient_id?: never }
) & {
  amount_per_person: string | null
  unit: string
  note: string | null
}

export interface RecipeInput {
  name: string
  description: string | null
  default_servings: number
  ingredients: RecipeLineInput[]
}

export const recipeKeys = {
  all: ['recipes'] as const,
  lists: () => [...recipeKeys.all, 'list'] as const,
  list: (q: string) => [...recipeKeys.lists(), { q }] as const,
  details: () => [...recipeKeys.all, 'detail'] as const,
  detail: (id: number) => [...recipeKeys.details(), id] as const,
  scaled: (id: number, servings: number) => [...recipeKeys.detail(id), 'scaled', servings] as const,
}

export function useRecipes(q: string) {
  return useInfiniteQuery({
    queryKey: recipeKeys.list(q),
    queryFn: ({ pageParam, signal }) => {
      const params = new URLSearchParams({ page: String(pageParam), page_size: String(PAGE_SIZE) })
      if (q) params.set('q', q)
      return api.get<RecipePage>(`/recipes?${params}`, signal)
    },
    initialPageParam: 1,
    getNextPageParam: (last) =>
      last.page * last.page_size < last.total ? last.page + 1 : undefined,
    placeholderData: keepPreviousData,
  })
}

export function useRecipe(id: number) {
  return useQuery({
    queryKey: recipeKeys.detail(id),
    queryFn: ({ signal }) => api.get<Recipe>(`/recipes/${id}`, signal),
  })
}

export function useScaledRecipe(id: number, servings: number, enabled = true) {
  return useQuery({
    queryKey: recipeKeys.scaled(id, servings),
    queryFn: ({ signal }) =>
      api.get<ScaledRecipe>(`/recipes/${id}/scaled?servings=${servings}`, signal),
    enabled,
    placeholderData: keepPreviousData,
  })
}

function afterSave(queryClient: QueryClient, input: Partial<RecipeInput>, saved: Recipe) {
  queryClient.setQueryData(recipeKeys.detail(saved.id), saved)
  const jobs = [
    queryClient.invalidateQueries({ queryKey: recipeKeys.lists() }),
    queryClient.invalidateQueries({ queryKey: recipeKeys.detail(saved.id), refetchType: 'none' }),
  ]
  if (input.ingredients?.some((line) => line.new_ingredient)) {
    jobs.push(queryClient.invalidateQueries({ queryKey: ingredientKeys.all }))
  }
  return Promise.all(jobs)
}

export function useCreateRecipe() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: RecipeInput) => api.post<Recipe>('/recipes', input),
    onSuccess: (saved, input) => afterSave(queryClient, input, saved),
  })
}

export function useUpdateRecipe(id: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: Partial<RecipeInput>) => api.patch<Recipe>(`/recipes/${id}`, input),
    onSuccess: (saved, input) => afterSave(queryClient, input, saved),
  })
}

export type DeleteResult = { archived: false } | { archived: true; recipe: Recipe }

/** 204 means gone; 200 with the recipe means it is still referenced and was archived instead. */
export function useDeleteRecipe(id: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (): Promise<DeleteResult> => {
      const body = await api.delete<Recipe | undefined>(`/recipes/${id}`)
      return body ? { archived: true, recipe: body } : { archived: false }
    },
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: recipeKeys.detail(id) })
      return queryClient.invalidateQueries({ queryKey: recipeKeys.lists() })
    },
  })
}

export function recipeErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return "Can't reach the server. Check your connection."
    return error.detail ?? error.title
  }
  return 'Something went wrong. Try again.'
}
