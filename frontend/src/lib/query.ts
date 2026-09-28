import { MutationCache, QueryCache, QueryClient } from '@tanstack/react-query'

import { ApiError, isUnauthorized } from '@/lib/api'

export const meQueryKey = ['auth', 'me'] as const

function shouldRetry(failureCount: number, error: unknown) {
  if (error instanceof ApiError && error.isClientError) return false
  return failureCount < 2
}

export function createQueryClient() {
  // A 401 from any request means the session is gone: dropping `me` to null
  // makes RequireAuth send the user to /login with the current path.
  const onError = (error: unknown) => {
    if (isUnauthorized(error)) queryClient.setQueryData(meQueryKey, null)
  }
  const queryClient: QueryClient = new QueryClient({
    queryCache: new QueryCache({ onError }),
    mutationCache: new MutationCache({ onError }),
    defaultOptions: {
      queries: {
        retry: shouldRetry,
        refetchOnWindowFocus: true,
        staleTime: 30_000,
      },
      mutations: { retry: false },
    },
  })
  return queryClient
}
