import type { Dimension } from '@/features/ingredients/api'
import type { DisplayQuantity } from '@/features/shopping/api'
import { displayFromBase, formatQuantity, toMilli } from '@/features/stock/quantity'

export const UNCATEGORISED = 'Other'

/** Groups in first-seen order; the backend already sorts by category, then name. */
export function groupByCategory<T extends { category: string | null }>(items: T[]) {
  const groups = new Map<string, T[]>()
  for (const item of items) {
    const key = item.category ?? UNCATEGORISED
    const group = groups.get(key)
    if (group) group.push(item)
    else groups.set(key, [item])
  }
  return [...groups]
}

export function formatBase(base: string, dimension: Dimension): string {
  const shown = displayFromBase(toMilli(base), dimension)
  return formatQuantity(shown.amount, shown.unit)
}

export function formatDisplay(q: DisplayQuantity): string {
  return formatQuantity(q.amount, q.unit.replaceAll('_', ' '))
}

const REASONS: Record<string, string> = {
  dimensionless: 'no measurable unit',
  missing_grams_per_ml: 'needs grams per ml',
  missing_grams_per_piece: 'needs grams per piece',
}

export function reasonLabel(reason: string): string {
  return REASONS[reason] ?? reason.replaceAll('_', ' ')
}
