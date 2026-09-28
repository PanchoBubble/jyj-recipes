import { useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { mealErrorMessage, mealSlotKeys, plannedMealKeys } from '@/features/calendar/api'
import type { MealSlot } from '@/features/calendar/plan'
import { api } from '@/lib/api'

function setSlots(queryClient: QueryClient, update: (slots: MealSlot[]) => MealSlot[]) {
  queryClient.setQueryData<MealSlot[]>(mealSlotKeys.all, (old) => old && update(old))
}

// Planned meals embed their slot, so a rename or toggle has to reach them too.
function refreshSlots(queryClient: QueryClient) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: mealSlotKeys.all }),
    queryClient.invalidateQueries({ queryKey: plannedMealKeys.all }),
  ])
}

export function useCreateMealSlot() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (name: string) => api.post<MealSlot>('/meal-slots', { name }),
    onSuccess: (slot) => setSlots(queryClient, (slots) => [...slots, slot]),
    onSettled: () => queryClient.invalidateQueries({ queryKey: mealSlotKeys.all }),
  })
}

export interface SlotChanges {
  name?: string
  active?: boolean
}

export function useUpdateMealSlot() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ slot, changes }: { slot: MealSlot; changes: SlotChanges }) =>
      api.patch<MealSlot>(`/meal-slots/${slot.id}`, changes),
    onMutate: async ({ slot, changes }) => {
      await queryClient.cancelQueries({ queryKey: mealSlotKeys.all })
      const previous = queryClient.getQueryData<MealSlot[]>(mealSlotKeys.all)
      setSlots(queryClient, (slots) => slots.map((s) => (s.id === slot.id ? { ...s, ...changes } : s)))
      return { previous }
    },
    onSuccess: (saved) => setSlots(queryClient, (slots) => slots.map((s) => (s.id === saved.id ? saved : s))),
    onError: (error, { slot }, context) => {
      if (context?.previous) queryClient.setQueryData(mealSlotKeys.all, context.previous)
      toast.error(`Couldn't update ${slot.name}`, { description: mealErrorMessage(error) })
    },
    onSettled: () => refreshSlots(queryClient),
  })
}

/** Errors are left to the caller: a 409 means the slot is in use and can only be deactivated. */
export function useDeleteMealSlot() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (slot: MealSlot) => api.delete<void>(`/meal-slots/${slot.id}`),
    onSuccess: (_data, slot) => setSlots(queryClient, (slots) => slots.filter((s) => s.id !== slot.id)),
    onSettled: () => refreshSlots(queryClient),
  })
}

export function useReorderMealSlots() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: [...mealSlotKeys.all, 'order'],
    mutationFn: (ordered: MealSlot[]) =>
      api.put<MealSlot[]>('/meal-slots/order', { ids: ordered.map((s) => s.id) }),
    onMutate: async (ordered) => {
      await queryClient.cancelQueries({ queryKey: mealSlotKeys.all })
      const previous = queryClient.getQueryData<MealSlot[]>(mealSlotKeys.all)
      queryClient.setQueryData<MealSlot[]>(
        mealSlotKeys.all,
        ordered.map((s, position) => ({ ...s, position })),
      )
      return { previous }
    },
    onError: (error, _ordered, context) => {
      if (context?.previous) queryClient.setQueryData(mealSlotKeys.all, context.previous)
      toast.error("Couldn't reorder meal slots", { description: mealErrorMessage(error) })
    },
    onSettled: () => {
      if (queryClient.isMutating({ mutationKey: [...mealSlotKeys.all, 'order'] }) > 1) return
      return queryClient.invalidateQueries({ queryKey: mealSlotKeys.all })
    },
  })
}
