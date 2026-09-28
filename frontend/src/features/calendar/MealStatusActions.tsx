import { useIsMutating } from '@tanstack/react-query'
import { Ban, ChefHat, RotateCcw, Undo2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

import { useCookPlannedMeal, useUpdatePlannedMeal } from './api'
import { isPending, type PlannedMeal } from './plan'

/** Status row of the meal sheet: cook, undo and skip through the calendar's mutations. */
export function MealStatusActions({ meal }: { meal: PlannedMeal }) {
  const cook = useCookPlannedMeal()
  const update = useUpdatePlannedMeal()
  // Any in-flight write on this meal, including one started before the sheet was reopened.
  const writing = useIsMutating({
    predicate: (m) => (m.state.variables as { meal?: PlannedMeal } | undefined)?.meal?.id === meal.id,
  })
  const busy = writing > 0 || isPending(meal)
  const { status } = meal

  return (
    <div className="flex flex-col gap-2" data-slot="meal-status-actions">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">Status</span>
        <Badge variant={status === 'planned' ? 'outline' : 'secondary'} className="capitalize">
          {status}
        </Badge>
      </div>

      {status === 'planned' && (
        <div className="grid grid-cols-2 gap-2">
          <Button
            className="h-11"
            disabled={busy}
            onClick={() => cook.mutate({ meal, action: 'cook' })}
          >
            <ChefHat aria-hidden /> Mark cooked
          </Button>
          <Button
            variant="outline"
            className="h-11"
            disabled={busy}
            onClick={() => update.mutate({ meal, changes: { status: 'skipped' } })}
          >
            <Ban aria-hidden /> Skip
          </Button>
        </div>
      )}

      {status === 'cooked' && (
        <Button
          variant="outline"
          className="h-11"
          disabled={busy}
          onClick={() => cook.mutate({ meal, action: 'uncook' })}
        >
          <Undo2 aria-hidden /> Undo cooked
        </Button>
      )}

      {status === 'skipped' && (
        <Button
          variant="outline"
          className="h-11"
          disabled={busy}
          onClick={() => update.mutate({ meal, changes: { status: 'planned' } })}
        >
          <RotateCcw aria-hidden /> Unskip
        </Button>
      )}
    </div>
  )
}
