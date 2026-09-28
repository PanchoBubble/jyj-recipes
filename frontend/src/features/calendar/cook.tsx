import { toast } from 'sonner'

import type { Dimension } from '@/features/ingredients/api'
import type { DisplayQuantity } from '@/features/shopping/api'
import { formatBase, formatDisplay, reasonLabel } from '@/features/shopping/format'
import { toMilli } from '@/features/stock/quantity'

import type { PlannedMeal } from './plan'

export interface StockImpact {
  ingredient_id: number
  name: string
  delta_base: string
  shortfall_base: string
  base_unit: string
  display: DisplayQuantity
}

export interface SkippedLine {
  recipe_ingredient_id: number
  ingredient_id: number
  name: string
  amount: string | null
  unit: string
  reason: string
}

export interface CookResult {
  meal: PlannedMeal
  changed: boolean
  stock: StockImpact[]
  skipped: SkippedLine[]
}

const DIMENSION_BY_BASE: Record<string, Dimension> = { g: 'mass', ml: 'volume', piece: 'count' }

export interface CookSummary {
  title: string
  lines: string[]
  short: boolean
}

export function cookSummary(result: CookResult, action: 'cook' | 'uncook'): CookSummary {
  const name = result.meal.recipe.name
  if (!result.changed) {
    return {
      title: action === 'cook' ? `${name} was already cooked` : `${name} wasn't cooked`,
      lines: [],
      short: false,
    }
  }

  const lines: string[] = []
  let short = false
  for (const impact of result.stock) {
    const delta = toMilli(impact.delta_base)
    if (delta !== 0n) {
      const amount = formatDisplay({ ...impact.display, amount: impact.display.amount.replace(/^-/, '') })
      lines.push(`${delta < 0n ? 'Used from pantry' : 'Returned to pantry'}: ${amount} ${impact.name}`)
    }
    const dimension = DIMENSION_BY_BASE[impact.base_unit]
    if (action === 'cook' && dimension && toMilli(impact.shortfall_base) > 0n) {
      short = true
      lines.push(`Short ${formatBase(impact.shortfall_base, dimension)} ${impact.name}`)
    }
  }
  if (result.skipped.length > 0) {
    const skipped = result.skipped.map((s) => `${s.name} (${reasonLabel(s.reason)})`)
    lines.push(`Pantry not changed for ${skipped.join(', ')}`)
  }

  return {
    title: action === 'cook' ? `Cooked ${name}` : `Undid cooking ${name}`,
    lines,
    short,
  }
}

export function toastCookResult(result: CookResult, action: 'cook' | 'uncook') {
  const { title, lines, short } = cookSummary(result, action)
  const description =
    lines.length > 0 ? (
      <ul aria-label="Pantry changes" className="flex flex-col gap-0.5">
        {lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    ) : undefined
  const show = !result.changed ? toast.info : short ? toast.warning : toast.success
  show(title, { description, duration: short ? 10_000 : 6_000 })
}
