import {
  useMutation,
  useMutationState,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'
import { toast } from 'sonner'

import { ingredientKeys, type Dimension } from '@/features/ingredients/api'
import { stockKeys } from '@/features/stock/api'
import { toMilli } from '@/features/stock/quantity'
import { ApiError, api } from '@/lib/api'

export interface DisplayQuantity {
  amount: string
  unit: string
}

export interface ShoppingRange {
  from: string
  to: string
}

export interface MealRef {
  planned_meal_id: number
  date: string
  slot_id: number
  slot_name: string
  recipe_id: number
  recipe_name: string
  servings: number
}

export type UnconvertedReason = 'dimensionless' | 'missing_grams_per_ml' | 'missing_grams_per_piece'

export interface UnconvertedLine {
  amount: string
  unit: string
  reason: UnconvertedReason | string
  recipe_names: string[]
}

export interface PreviewItem {
  ingredient_id: number
  ingredient_name: string
  category: string | null
  dimension: Dimension
  base_unit: string
  required_base: string
  stock_base: string
  reserved_base: string
  available_base: string
  to_buy_base: string
  display: DisplayQuantity
  nothing_to_buy: boolean
  contributions: { meal: MealRef; amount: string; unit: string; amount_base: string }[]
  unconverted: (UnconvertedLine & { meals: MealRef[] })[]
}

export interface CheckHaveItem {
  ingredient_id: number
  ingredient_name: string
  category: string | null
  recipe_names: string[]
  meals: MealRef[]
}

export interface ShoppingPreview {
  start_date: string
  end_date: string
  today: string
  items: PreviewItem[]
  check_have: CheckHaveItem[]
}

export type ShoppingItemKind = 'buy' | 'unconverted' | 'check_have'
export type ShoppingListStatus = 'open' | 'done'

export interface ShoppingListItem {
  id: number
  list_id: number
  position: number
  kind: ShoppingItemKind
  ingredient_id: number
  ingredient_name: string
  category: string | null
  base_unit: string
  required_base: string
  available_base: string
  to_buy_base: string
  display: DisplayQuantity
  checked: boolean
  bought_base: string | null
  bought_display: DisplayQuantity | null
  unconverted: UnconvertedLine[]
  recipe_names: string[]
}

export interface ShoppingListSummary {
  id: number
  start_date: string
  end_date: string
  status: ShoppingListStatus
  created_by: number
  created_at: string
  completed_at: string | null
  item_count: number
  checked_count: number
}

export interface ShoppingList extends ShoppingListSummary {
  items: ShoppingListItem[]
}

export interface ItemUpdate {
  checked?: boolean
  bought_quantity?: string | null
  unit?: string
}

export const shoppingKeys = {
  all: ['shopping'] as const,
  previews: () => [...shoppingKeys.all, 'preview'] as const,
  preview: (range: ShoppingRange) => [...shoppingKeys.previews(), range] as const,
  lists: () => [...shoppingKeys.all, 'lists'] as const,
  list: (id: number) => [...shoppingKeys.all, 'list', id] as const,
}

export const itemMutationKey = (listId: number) => ['shopping', 'item', listId] as const

export function shoppingErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return "Can't reach the server."
    return error.detail ?? error.title
  }
  return 'Something went wrong.'
}

export function useShoppingPreview(range: ShoppingRange | null) {
  return useQuery({
    queryKey: shoppingKeys.preview(range ?? { from: '', to: '' }),
    queryFn: () => api.post<ShoppingPreview>('/shopping/preview', range),
    enabled: range !== null,
    placeholderData: (previous) => previous,
  })
}

export function useShoppingLists() {
  return useQuery({
    queryKey: shoppingKeys.lists(),
    queryFn: ({ signal }) => api.get<ShoppingListSummary[]>('/shopping-lists', signal),
  })
}

export function useShoppingList(id: number) {
  return useQuery({
    queryKey: shoppingKeys.list(id),
    queryFn: ({ signal }) => api.get<ShoppingList>(`/shopping-lists/${id}`, signal),
    // The open list is what the store trip runs on; keep it around even if the tab idles.
    gcTime: 24 * 60 * 60 * 1000,
  })
}

export function useCreateShoppingList() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (range: ShoppingRange) => api.post<ShoppingList>('/shopping-lists', range),
    onSuccess: (list) => {
      queryClient.setQueryData(shoppingKeys.list(list.id), list)
      return queryClient.invalidateQueries({ queryKey: shoppingKeys.lists() })
    },
  })
}

export function useDeleteShoppingList(id: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.delete<void>(`/shopping-lists/${id}`),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: shoppingKeys.list(id) })
      return queryClient.invalidateQueries({ queryKey: shoppingKeys.lists() })
    },
  })
}

/** Network failures and server errors are worth retrying; a 4xx will fail the same way again. */
function isTransient(error: unknown) {
  return !(error instanceof ApiError && error.isClientError)
}

export const ITEM_RETRY_LIMIT = 8

function patchItem(
  queryClient: QueryClient,
  listId: number,
  itemId: number,
  update: (item: ShoppingListItem) => ShoppingListItem,
) {
  queryClient.setQueryData<ShoppingList>(shoppingKeys.list(listId), (list) =>
    list && {
      ...list,
      items: list.items.map((i) => (i.id === itemId ? update(i) : i)),
    },
  )
}

interface ItemMutation {
  item: ShoppingListItem
  changes: ItemUpdate
}

function optimisticItem(item: ShoppingListItem, changes: ItemUpdate): ShoppingListItem {
  const next = { ...item }
  if (changes.checked !== undefined) next.checked = changes.checked
  if (changes.bought_quantity === null) {
    next.bought_base = null
    next.bought_display = null
  } else if (changes.bought_quantity !== undefined) {
    next.bought_display = {
      amount: changes.bought_quantity,
      unit: changes.unit ?? item.display.unit,
    }
  }
  return next
}

/**
 * Item edits apply to the cached list at once. Edits to one item run in order (one scope per
 * item) while other items stay independent; transient failures keep retrying and stay visible as pending, and a rejected edit
 * rolls the item back to what it was before that edit.
 */
export function useUpdateShoppingItem(listId: number, itemId: number) {
  const queryClient = useQueryClient()
  const mutationKey = itemMutationKey(listId)
  const pendingFor = (itemId: number) =>
    queryClient.isMutating({
      mutationKey,
      predicate: (m) => (m.state.variables as ItemMutation | undefined)?.item.id === itemId,
    })

  return useMutation({
    mutationKey,
    scope: { id: `shopping-item-${itemId}` },
    mutationFn: ({ item, changes }: ItemMutation) =>
      api.patch<ShoppingListItem>(`/shopping-lists/${listId}/items/${item.id}`, changes),
    retry: (failureCount, error) => isTransient(error) && failureCount < ITEM_RETRY_LIMIT,
    retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 15_000),
    onMutate: async ({ item, changes }) => {
      await queryClient.cancelQueries({ queryKey: shoppingKeys.list(listId) })
      const current = queryClient
        .getQueryData<ShoppingList>(shoppingKeys.list(listId))
        ?.items.find((i) => i.id === item.id)
      const previous = current ?? item
      patchItem(queryClient, listId, item.id, (i) => optimisticItem(i, changes))
      return { previous }
    },
    onError: (error, { item }, context) => {
      if (context?.previous) {
        const previous = context.previous
        patchItem(queryClient, listId, item.id, () => previous)
      }
      toast.error(`Couldn't update ${item.ingredient_name}`, {
        description: shoppingErrorMessage(error),
      })
    },
    onSuccess: (saved, { item }) => {
      // A later edit to the same item is still queued: its optimistic value must stay.
      if (pendingFor(item.id) === 1) patchItem(queryClient, listId, item.id, () => saved)
    },
    onSettled: () => {
      if (queryClient.isMutating({ mutationKey }) === 1) {
        return queryClient.invalidateQueries({ queryKey: shoppingKeys.lists() })
      }
    },
  })
}

export interface PendingItem {
  itemId: number
  paused: boolean
  failureCount: number
}

export function usePendingItems(listId: number): Map<number, PendingItem> {
  const pending = useMutationState({
    filters: { mutationKey: itemMutationKey(listId), status: 'pending' },
    select: (m) => ({
      itemId: (m.state.variables as ItemMutation).item.id,
      paused: m.state.isPaused,
      failureCount: m.state.failureCount,
    }),
  })
  const byItem = new Map<number, PendingItem>()
  for (const p of pending) {
    const seen = byItem.get(p.itemId)
    byItem.set(p.itemId, {
      itemId: p.itemId,
      paused: p.paused || (seen?.paused ?? false),
      failureCount: Math.max(p.failureCount, seen?.failureCount ?? 0),
    })
  }
  return byItem
}

/** What complete will add to stock for an item, or null when it adds nothing. */
export function purchasedQuantity(item: ShoppingListItem): DisplayQuantity | null {
  if (!item.checked) return null
  if (item.bought_display) {
    return toMilli(item.bought_display.amount) > 0n ? item.bought_display : null
  }
  if (item.bought_base !== null) return null
  return toMilli(item.to_buy_base) > 0n ? item.display : null
}

export function useCompleteShoppingList(id: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<ShoppingList>(`/shopping-lists/${id}/complete`),
    onSuccess: (list) => {
      queryClient.setQueryData(shoppingKeys.list(id), list)
      const added = list.items.filter((i) => purchasedQuantity(i) !== null).length
      toast.success(
        added === 0
          ? 'Shopping done'
          : `Added ${added} ${added === 1 ? 'item' : 'items'} to stock`,
      )
      return Promise.all([
        queryClient.invalidateQueries({ queryKey: shoppingKeys.lists() }),
        queryClient.invalidateQueries({ queryKey: shoppingKeys.previews() }),
        queryClient.invalidateQueries({ queryKey: stockKeys.all }),
        queryClient.invalidateQueries({ queryKey: ingredientKeys.all }),
      ])
    },
  })
}
