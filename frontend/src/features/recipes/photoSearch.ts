import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'

import { plannedMealKeys } from '@/features/calendar/api'
import { recipeKeys, type PhotoProvider, type Recipe } from '@/features/recipes/api'
import { ApiError, api } from '@/lib/api'

export const PHOTO_QUERY_MAX = 100

export interface PhotoSearchResult {
  provider: PhotoProvider
  id: number | string
  alt: string
  width: number
  height: number
  photographer: string
  photographer_url: string | null
  page_url: string | null
  thumb_url: string
  preview_url: string
  title: string | null
  license: string | null
  license_url: string | null
}

export interface PhotoSearchPage {
  provider: PhotoProvider
  query: string
  page: number
  has_more: boolean
  results: PhotoSearchResult[]
}

export const PROVIDER_NAMES: Record<PhotoProvider, string> = {
  pexels: 'Pexels',
  openverse: 'Openverse',
}

export const photoSearchKeys = {
  all: ['photo-search'] as const,
  query: (q: string) => [...photoSearchKeys.all, { q }] as const,
}

export function usePhotoSearch(q: string) {
  return useInfiniteQuery({
    queryKey: photoSearchKeys.query(q),
    queryFn: async ({ pageParam, signal }) => {
      const params = new URLSearchParams({ q, page: String(pageParam) })
      return api.get<PhotoSearchPage>(`/images/search?${params}`, signal)
    },
    initialPageParam: 1,
    getNextPageParam: (last) => (last.has_more ? last.page + 1 : undefined),
    enabled: q.length > 0,
    staleTime: 5 * 60_000,
    retry: false,
  })
}

export interface UsePhotoVariables {
  recipeId: number
  provider: PhotoProvider
  photoId: number | string
}

export function useSetPhotoFromSearch() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ recipeId, provider, photoId }: UsePhotoVariables) =>
      api.post<Recipe>(`/recipes/${recipeId}/photo/from-search`, {
        provider,
        photo_id: photoId,
      }),
    onSuccess: (saved) => {
      queryClient.setQueryData(recipeKeys.detail(saved.id), saved)
      return Promise.all([
        queryClient.invalidateQueries({ queryKey: recipeKeys.lists() }),
        queryClient.invalidateQueries({ queryKey: recipeKeys.detail(saved.id), refetchType: 'none' }),
        queryClient.invalidateQueries({ queryKey: plannedMealKeys.all }),
      ])
    },
  })
}

export function photoSearchErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return "Can't reach the server. Check your connection."
    if (error.status === 429) return 'Too many searches. Wait a minute and try again.'
    if (error.detail) return error.detail
    return error.title
  }
  return 'Something went wrong. Try again.'
}
