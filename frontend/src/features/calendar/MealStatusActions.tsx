import { Badge } from '@/components/ui/badge'

import type { PlannedMeal } from './plan'

/**
 * Status row of the meal sheet. Cook, undo and skip actions belong here; each should
 * go through the calendar's planned-meal mutations so the week view stays in sync.
 */
export function MealStatusActions({ meal }: { meal: PlannedMeal }) {
  return (
    <div className="flex items-center justify-between gap-2" data-slot="meal-status-actions">
      <span className="text-sm font-medium">Status</span>
      <Badge
        variant={meal.status === 'planned' ? 'outline' : 'secondary'}
        className="capitalize"
      >
        {meal.status}
      </Badge>
    </div>
  )
}
